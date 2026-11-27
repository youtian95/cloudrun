# cloudrun

[简体中文](readme_cn.md)

Run local Python computations on a Linux cloud server. Cloudrun syncs changed project files, sets up the Python environment, streams logs during the job, and downloads results when the job exits.

| Provider | Alibaba Cloud | AWS | Azure | Google Cloud | Tencent Cloud | Huawei Cloud | DigitalOcean |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Instance automation | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

## Quick start

1. Install the CLI with `uv`.

   ```powershell
   uv tool install cloudrun
   ```

   The standard installation includes the Alibaba Cloud SDK. To install from a source checkout in editable mode, run `uv tool install --editable .` from the repository root.

2. Run `cloudrun init` in your project directory to generate `config_cloudrun.yaml`. If you have a Cloudrun configuration from an earlier release, rename it from `config.yaml` to `config_cloudrun.yaml` to keep your settings.

3. Fill in `config_cloudrun.yaml`. For an existing server, configure the SSH connection under `server` and set `provider.aliyun.enabled` to `false`. For Alibaba Cloud provisioning, set `provider.aliyun.enabled` to `true` and provide `ALIBABA_CLOUD_ACCESS_KEY_ID` and `ALIBABA_CLOUD_ACCESS_KEY_SECRET` as environment variables.

4. Submit your Python script or module from the project directory.

   ```powershell
   cloudrun run script.py
   cloudrun run -m my_package.my_module
   ```

## Commands

| Command | What it does |
|---|---|
| `init [directory] [--force]` | Generate `config_cloudrun.yaml`. |
| `size` | Estimate the full upload size locally. Applies `sync.exclude` and counts `env.wheels` separately. |
| `push` | Sync project files and the Python environment without starting a job. |
| `run [python arguments…]` | Sync and run a job, stream its logs, and download results when it exits. Set `run.detach` to start the job in the background. |
| `fetch` | Download new and changed files. |
| `logs [--tail N] [--follow]` | View or follow job logs. |
| `status` | Check whether the job is running. |
| `up` | Create or start an instance and obtain its public IP address. |
| `down` | Stop the instance. |
| `destroy [--cleanup]` | Release the instance. Use `--cleanup` to also remove the associated network resources and key pair. |
