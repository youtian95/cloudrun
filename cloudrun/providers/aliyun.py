"""阿里云 ECS 自动供应：自动建网络/密钥/实例、分配公网、开机/停机/释放。

功能：用 aliyun-python-sdk-core 的通用请求调用 ECS/VPC API——
      up 自动创建（或复用并启动）实例并拿到公网 IP；down 停机不收费；destroy 释放。
      网络（VPC/交换机/安全组）与密钥对全自动创建并按标签/状态文件复用。
使用流程：cloudrun up -> run（自动 up/down）-> cloudrun down/destroy。
输入：Config（aliyun 段，凭证可来自环境变量）、工作目录。
输出：.cloudrun/aliyun_state.json（实例/公网IP/密钥/网络 ID），并把连接信息写回 cfg。
注意：真实创建计费资源；默认按量付费 + 停机不收费；destroy 才会释放实例。
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid

from ..config import Config

# 默认配置（可被 config_cloudrun.yaml 的 provider.aliyun 段覆盖）
DEFAULTS = {
    "enabled": False,
    "access_key_id": "",
    "access_key_secret": "",
    "region_id": "cn-hangzhou",
    "zone_id": "",
    "instance_type": "ecs.e-c1m1.large",   # 便宜按量型，可改
    "image_id": "",                        # 空则自动找 Alibaba Cloud Linux 3
    "instance_charge_type": "PostPaid",
    "spot_strategy": "SpotAsPriceGo",      # 抢占式（默认，省钱，可能被回收）；普通按量设 NoSpot
    "spot_duration": 1,                    # 抢占保护期(小时)，期内不被回收；0=无保护
    "spot_interruption_behavior": "Stop",  # 中断时停机保留(契合复用)；Terminate=释放
    "internet_max_bandwidth_out": 5,       # >0 才会分配公网 IP
    "system_disk_category": "cloud_essd_entry",
    "system_disk_size": 40,
    "key_pair_name": "",                   # 空则自动创建并保存私钥
    "security_group_id": "",               # 空则自动创建
    "vswitch_id": "",                      # 空则自动创建（含 VPC）
    "vpc_cidr": "172.16.0.0/12",
    "vswitch_cidr": "172.16.0.0/24",
    "instance_name": "cloudrun",
    "reuse": True,                         # True=复用停机实例；False=每次新建
    "user": "root",
}


def _conf(cfg: Config) -> dict:
    """取 provider.aliyun 配置段。"""
    return cfg.provider.get("aliyun", {})


def _settings(cfg: Config) -> dict:
    """合并默认配置与用户 aliyun 段，并允许用环境变量补凭证。"""
    s = dict(DEFAULTS)
    s.update(_conf(cfg))
    s["access_key_id"] = s["access_key_id"] or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID", "")
    s["access_key_secret"] = s["access_key_secret"] or os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "")
    return s


def is_enabled(cfg: Config) -> bool:
    """判断阿里云 provider 是否启用。"""
    return bool(_conf(cfg).get("enabled"))


def reuse(cfg: Config) -> bool:
    """判断是否复用停机实例；False 表示每次新建、用完即释放。"""
    return bool(_conf(cfg).get("reuse", True))


def _client(s: dict) -> AcsClient:
    """创建阿里云 SDK 客户端。

    参数：
        s: 合并后的设置字典（含凭证与地域）。

    返回：
        AcsClient 实例。

    异常：
        ValueError: 凭证缺失。
    """
    from aliyunsdkcore.client import AcsClient
    if not s["access_key_id"] or not s["access_key_secret"]:
        raise ValueError("缺少阿里云凭证：请设环境变量 ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET 或在 config.provider.aliyun 填写")
    return AcsClient(s["access_key_id"], s["access_key_secret"], s["region_id"])


def _call(client: AcsClient, s: dict, product: str, version: str, action: str, params: dict) -> dict:
    """用 CommonRequest 发起一次通用 API 调用。

    参数：
        client: AcsClient。
        s: 设置字典（用 region_id 拼接入域）。
        product: 产品代号，如 ecs 或 vpc。
        version: API 版本号。
        action: 动作名，如 DescribeInstances。
        params: 查询参数，值为 None 或空串的项会被跳过。

    返回：
        响应解析后的字典。
    """
    from aliyunsdkcore.request import CommonRequest
    req = CommonRequest()
    req.set_domain(f"{product}.{s['region_id']}.aliyuncs.com")
    req.set_version(version)
    req.set_action_name(action)
    req.set_method("POST")
    for k, v in params.items():
        if v is not None and v != "":
            req.add_query_param(k, v)
    return json.loads(client.do_action_with_exception(req))


def _ecs(client, s, action, params):
    """调用 ECS API（版本 2014-05-26），参数与返回值同 _call。"""
    return _call(client, s, "ecs", "2014-05-26", action, params)


def _vpc(client, s, action, params):
    """调用 VPC API（版本 2016-04-28），参数与返回值同 _call。"""
    return _call(client, s, "vpc", "2016-04-28", action, params)


# ---------- 状态文件 ----------
def _state_path(workdir: str) -> str:
    """状态文件路径：workdir/.cloudrun/aliyun_state.json。"""
    return os.path.join(workdir, ".cloudrun", "aliyun_state.json")


def _load_state(workdir: str) -> dict:
    """读取状态文件，不存在时返回空字典。"""
    p = _state_path(workdir)
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_state(workdir: str, state: dict) -> None:
    """把状态字典写回状态文件，父目录自动创建。"""
    p = _state_path(workdir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _wait(fn, timeout: int = 300, interval: int = 5) -> None:
    """轮询等待 fn() 返回 True。

    参数：
        fn: 无参判定函数，返回 True 表示就绪。
        timeout: 最长等待秒数。
        interval: 轮询间隔秒数。

    异常：
        TimeoutError: 超过 timeout 仍未就绪。
    """
    start = time.time()
    while time.time() - start < timeout:
        if fn():
            return
        time.sleep(interval)
    raise TimeoutError("等待阿里云资源就绪超时")


# ---------- 网络 / 密钥 / 镜像 / 可用区 ----------
def _ensure_network(client: AcsClient, s: dict, state: dict) -> None:
    """确保 VPC、交换机、安全组就绪并放行 22 端口；配置里已给出 ID 时直接沿用。

    参数：
        client: AcsClient。
        s: 设置字典（读 region_id 与各 cidr）。
        state: 状态字典，新建的资源 ID 写回这里以便复用。
    """
    if s["vswitch_id"] and s["security_group_id"]:
        state["vswitch_id"] = s["vswitch_id"]
        state["security_group_id"] = s["security_group_id"]
        return
    # VPC
    vpc_id = state.get("vpc_id")
    if not vpc_id:
        vpc_id = _vpc(client, s, "CreateVpc", {"RegionId": s["region_id"], "CidrBlock": s["vpc_cidr"], "VpcName": "cloudrun-vpc"})["VpcId"]
        state["vpc_id"] = vpc_id
        _wait(lambda: _vpc(client, s, "DescribeVpcs", {"RegionId": s["region_id"], "VpcId": vpc_id})["Vpcs"]["Vpc"][0]["Status"] == "Available")
    # 交换机（需可用区）
    vswitch_id = state.get("vswitch_id")
    if not vswitch_id:
        zone = s["zone_id"] or _pick_zone(client, s)
        state["zone_id"] = zone
        vswitch_id = _vpc(client, s, "CreateVSwitch", {"RegionId": s["region_id"], "ZoneId": zone, "VpcId": vpc_id, "CidrBlock": s["vswitch_cidr"], "VSwitchName": "cloudrun-vsw"})["VSwitchId"]
        state["vswitch_id"] = vswitch_id
        _wait(lambda: _vpc(client, s, "DescribeVSwitches", {"RegionId": s["region_id"], "VSwitchId": vswitch_id})["VSwitches"]["VSwitch"][0]["Status"] == "Available")
    # 安全组 + 放行 SSH
    sg_id = state.get("security_group_id")
    if not sg_id:
        sg_id = _ecs(client, s, "CreateSecurityGroup", {"RegionId": s["region_id"], "VpcId": vpc_id, "SecurityGroupName": "cloudrun-sg"})["SecurityGroupId"]
        state["security_group_id"] = sg_id
        _ecs(client, s, "AuthorizeSecurityGroup", {"RegionId": s["region_id"], "SecurityGroupId": sg_id, "IpProtocol": "tcp", "PortRange": "22/22", "SourceCidrIp": "0.0.0.0/0", "NicType": "intranet", "Policy": "accept"})
    state["vswitch_id"] = vswitch_id
    state["security_group_id"] = sg_id


def _ensure_keypair(client: AcsClient, s: dict, state: dict, workdir: str) -> None:
    """确保密钥对可用，没有则自动创建并把私钥存到 .cloudrun/。

    参数：
        client: AcsClient。
        s: 设置字典（读 key_pair_name）。
        state: 状态字典，写入 key_pair_name 与 key_file。
        workdir: 本地工作目录，私钥落在其 .cloudrun/ 下。
    """
    if s["key_pair_name"]:
        state["key_pair_name"] = s["key_pair_name"]
        return
    if state.get("key_pair_name") and state.get("key_file") and os.path.isfile(state["key_file"]):
        return
    name = "cloudrun-" + uuid.uuid4().hex[:8]
    resp = _ecs(client, s, "CreateKeyPair", {"RegionId": s["region_id"], "KeyPairName": name})
    key_path = os.path.join(workdir, ".cloudrun", "cloudrun_key.pem")
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    with open(key_path, "w", encoding="utf-8") as f:
        f.write(resp["PrivateKeyBody"])
    state["key_pair_name"] = name
    state["key_file"] = key_path


def _ensure_image(client: AcsClient, s: dict) -> str:
    """确定镜像 ID；未指定时挑选最新的 Alibaba Cloud Linux 3。"""
    if s["image_id"]:
        return s["image_id"]
    resp = _ecs(client, s, "DescribeImages", {"RegionId": s["region_id"], "ImageOwnerAlias": "system", "OSType": "linux", "Architecture": "x86_64", "Status": "Available", "PageSize": 100})
    images = resp["Images"]["Image"]
    alinux3 = [im for im in images if "Alibaba Cloud Linux 3" in im.get("OSName", "") or "Alinux 3" in im.get("OSName", "")]
    pool = alinux3 or images
    pool.sort(key=lambda im: im.get("CreationTime", ""), reverse=True)
    return pool[0]["ImageId"]


def _pick_zone(client: AcsClient, s: dict) -> str:
    """挑一个支持目标实例规格的可用区。"""
    resp = _ecs(client, s, "DescribeZones", {"RegionId": s["region_id"]})
    for zone in resp["Zones"]["Zone"]:
        types = zone.get("AvailableInstanceTypes", {}).get("InstanceTypes", [])
        if s["instance_type"] in types:
            return zone["ZoneId"]
    return resp["Zones"]["Zone"][0]["ZoneId"]


# ---------- 实例 ----------
def _describe_instance(client: AcsClient, s: dict, instance_id: str) -> dict | None:
    """查询实例详情，实例不存在时返回 None。"""
    resp = _ecs(client, s, "DescribeInstances", {"RegionId": s["region_id"], "InstanceIds": json.dumps([instance_id])})
    instances = resp["Instances"]["Instance"]
    return instances[0] if instances else None


def _run_instances(client: AcsClient, s: dict, state: dict, image_id: str) -> str:
    """创建一台按量实例（带公网带宽、密钥对与 cloudrun 标签）。

    参数：
        client: AcsClient。
        s: 设置字典（规格/带宽/磁盘等）。
        state: 状态字典（读安全组、交换机、可用区、密钥对）。
        image_id: 镜像 ID。

    返回：
        新建实例的 InstanceId。
    """
    params = {
        "RegionId": s["region_id"], "InstanceType": s["instance_type"], "ImageId": image_id,
        "SecurityGroupId": state["security_group_id"], "VSwitchId": state["vswitch_id"],
        "InstanceChargeType": s["instance_charge_type"], "InternetMaxBandwidthOut": s["internet_max_bandwidth_out"],
        "SystemDisk.Category": s["system_disk_category"], "SystemDisk.Size": s["system_disk_size"],
        "InstanceName": s["instance_name"], "Amount": 1, "UniqueSuffix": True,
        "SpotStrategy": s["spot_strategy"], "Tag.1.Key": "cloudrun", "Tag.1.Value": "true",
    }
    if s["spot_strategy"] != "NoSpot":
        params["SpotDuration"] = s["spot_duration"]
        params["SpotInterruptionBehavior"] = s["spot_interruption_behavior"]
    if state.get("key_pair_name"):
        params["KeyPairName"] = state["key_pair_name"]
    if state.get("zone_id"):
        params["ZoneId"] = state["zone_id"]
    resp = _ecs(client, s, "RunInstances", params)
    return resp["InstanceIdSets"]["InstanceIdSet"][0]


def _wait_running(client: AcsClient, s: dict, instance_id: str) -> None:
    """等待实例进入 Running 状态。

    参数：
        client: AcsClient。
        s: 设置字典。
        instance_id: 实例 ID。
    """
    def ok():
        """实例是否已进入 Running。"""
        inst = _describe_instance(client, s, instance_id)
        return bool(inst and inst["Status"] == "Running")
    _wait(ok, timeout=300)


def _ensure_public_ip(client: AcsClient, s: dict, inst: dict) -> str:
    """取实例的公网 IP，没有则现场申请一个。"""
    ips = inst.get("PublicIpAddress", {}).get("IpAddress", [])
    ip = ips[0] if ips else inst.get("EipAddress", {}).get("IpAddress", "")
    if not ip:
        ip = _ecs(client, s, "AllocatePublicIpAddress", {"InstanceId": inst["InstanceId"]})["IpAddress"]
    return ip


def _wait_ssh(ip: str, port: int = 22, timeout: int = 180) -> None:
    """等待 SSH 端口可连通。

    参数：
        ip: 公网 IP。
        port: SSH 端口。
        timeout: 最长等待秒数。

    异常：
        TimeoutError: 超时仍连不上。
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((ip, port), timeout=5):
                return
        except OSError:
            time.sleep(5)
    raise TimeoutError(f"等待 SSH {ip}:{port} 可达超时")


# ---------- 对外命令 ----------
def up(cfg: Config, workdir: str) -> dict:
    """供应并开机：复用则启动已有实例，否则全自动新建网络/密钥/实例。

    参数：
        cfg: 配置。
        workdir: 工作目录（状态文件存放处）。

    返回：
        最新状态字典（含 instance_id、public_ip 等）。
    """
    s = _settings(cfg)
    client = _client(s)
    state = _load_state(workdir)
    inst = None
    if s["reuse"] and state.get("instance_id"):
        inst = _describe_instance(client, s, state["instance_id"])
    if inst:
        if inst["Status"] == "Stopped":
            print("启动已有实例（停机不收费恢复）……")
            _ecs(client, s, "StartInstance", {"InstanceId": inst["InstanceId"]})
        _wait_running(client, s, inst["InstanceId"])
        inst = _describe_instance(client, s, inst["InstanceId"])
        ip = _ensure_public_ip(client, s, inst)
        state.update(instance_id=inst["InstanceId"], public_ip=ip)
    else:
        print("全自动创建网络/密钥/实例……")
        _ensure_network(client, s, state)
        _ensure_keypair(client, s, state, workdir)
        image_id = _ensure_image(client, s)
        iid = _run_instances(client, s, state, image_id)
        state["instance_id"] = iid
        _wait_running(client, s, iid)
        inst = _describe_instance(client, s, iid)
        ip = _ensure_public_ip(client, s, inst)
        state.update(public_ip=ip, region_id=s["region_id"], instance_type=s["instance_type"])
    _save_state(workdir, state)
    _wait_ssh(ip)
    apply_state(cfg, workdir)
    print(f"实例就绪：{state['instance_id']}  公网 IP：{ip}")
    return state


def down(cfg: Config, workdir: str) -> None:
    """停机不收费（StopCharging），保留磁盘与状态以便下次开机。"""
    s = _settings(cfg)
    state = _load_state(workdir)
    if not state.get("instance_id"):
        print("无实例可停")
        return
    client = _client(s)
    _ecs(client, s, "StopInstance", {"InstanceId": state["instance_id"], "StoppedMode": "StopCharging"})
    print("已停机（StopCharging，不再计 CPU/内存费）")


def destroy(cfg: Config, workdir: str, cleanup: bool = False) -> None:
    """释放实例；cleanup 为 True 时一并删除安全组、交换机、VPC 与密钥对。

    参数：
        cfg: 配置。
        workdir: 工作目录（状态文件存放处）。
        cleanup: 是否连同网络与密钥一起清理。
    """
    s = _settings(cfg)
    state = _load_state(workdir)
    client = _client(s)
    if state.get("instance_id"):
        _ecs(client, s, "DeleteInstance", {"InstanceId": state["instance_id"], "Force": True})
        print("已释放实例", state["instance_id"])
    if cleanup:
        if state.get("security_group_id"):
            _ecs(client, s, "DeleteSecurityGroup", {"RegionId": s["region_id"], "SecurityGroupId": state["security_group_id"]})
        if state.get("vswitch_id"):
            _vpc(client, s, "DeleteVSwitch", {"RegionId": s["region_id"], "VSwitchId": state["vswitch_id"]})
        if state.get("vpc_id"):
            _vpc(client, s, "DeleteVpc", {"RegionId": s["region_id"], "VpcId": state["vpc_id"]})
        if state.get("key_pair_name"):
            _ecs(client, s, "DeleteKeyPairs", {"RegionId": s["region_id"], "KeyPairNames": json.dumps([state["key_pair_name"]])})
        print("已清理网络/密钥")
    _save_state(workdir, {})


def apply_state(cfg: Config, workdir: str) -> None:
    """把状态文件里的公网 IP、密钥与登录用户写回 cfg，供后续 SSH 使用。

    参数：
        cfg: 配置，会被就地修改。
        workdir: 工作目录（状态文件存放处）。
    """
    if not _conf(cfg).get("enabled"):
        return
    state = _load_state(workdir)
    if state.get("public_ip"):
        cfg.server_host = state["public_ip"]
    if state.get("key_file"):
        cfg.server_key = state["key_file"]
    cfg.server_user = _settings(cfg)["user"]
