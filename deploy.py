#!/usr/bin/env python3
"""AIHR 一键部署到阿里云函数计算 FC (Custom Container + NAS)

用法:
  export ALIBABA_ACCESS_KEY_ID=LTAI5t...
  export ALIBABA_ACCESS_KEY_SECRET=...
  python deploy.py

前置: Docker Desktop 已启动
"""

import os, sys, json, time, base64, subprocess
from pathlib import Path

# Windows GBK 兼容：强制 stdout 用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── 配置 ──
PROJECT_DIR = Path(__file__).resolve().parent
REGION = "cn-hangzhou"
NAS_ZONE = f"{REGION}-i"

ACR_ENDPOINT = f"registry.{REGION}.aliyuncs.com"
ACR_NS = "aihr"
ACR_REPO = "aihr"
IMAGE_TAG = f"{ACR_ENDPOINT}/{ACR_NS}/{ACR_REPO}:latest"

FC_SVC = "aihr-svc"
FC_FN = "aihr-app"


# ═══════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════

def load_config():
    """加载 AK 和业务环境变量"""
    ak_id = os.environ.get("ALIBABA_ACCESS_KEY_ID", "").strip()
    ak_secret = os.environ.get("ALIBABA_ACCESS_KEY_SECRET", "").strip()
    if not ak_id or not ak_secret:
        print("❌ 请设置阿里云 AccessKey 环境变量：")
        print("   export ALIBABA_ACCESS_KEY_ID=LTAI5t...")
        print("   export ALIBABA_ACCESS_KEY_SECRET=...")
        sys.exit(1)

    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        print(f"❌ 缺少 .env: {env_file}")
        sys.exit(1)

    from dotenv import load_dotenv
    load_dotenv(env_file)

    app_env = {
        "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY", ""),
        "DEEPSEEK_BASE_URL": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "BAIDU_APP_ID": os.getenv("BAIDU_APP_ID", ""),
        "BAIDU_API_KEY": os.getenv("BAIDU_API_KEY", ""),
        "BAIDU_SECRET_KEY": os.getenv("BAIDU_SECRET_KEY", ""),
    }
    missing = [k for k, v in app_env.items() if not v]
    if missing:
        print(f"❌ .env 缺少: {', '.join(missing)}")
        sys.exit(1)

    return ak_id, ak_secret, app_env


def make_client(svc_cls, endpoint, ak_id, ak_secret):
    from alibabacloud_tea_openapi import models as m
    cfg = m.Config(access_key_id=ak_id, access_key_secret=ak_secret,
                   endpoint=endpoint, region_id=REGION)
    return svc_cls(cfg)


def get_account_id(ak_id, ak_secret):
    from alibabacloud_sts20150401.client import Client as STS
    sts = make_client(STS, f"sts.{REGION}.aliyuncs.com", ak_id, ak_secret)
    return sts.get_caller_identity().body.account_id


# ═══════════════════════════════════════════════════
# Step 1: Docker 构建
# ═══════════════════════════════════════════════════

def step_build():
    print("\n[1/5] Docker 构建...")
    try:
        subprocess.run(["docker", "version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("❌ Docker 未安装或未启动，请先启动 Docker Desktop")
        sys.exit(1)

    r = subprocess.run(
        ["docker", "build", "-t", "aihr:latest",
         "-f", str(PROJECT_DIR / "Dockerfile"), str(PROJECT_DIR)],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if r.returncode != 0:
        # 取最后 15 行报错
        lines = r.stderr.strip().split("\n")[-15:]
        print("❌ Docker 构建失败:\n" + "\n".join(lines))
        sys.exit(1)
    print("   ✅ 构建完成")


# ═══════════════════════════════════════════════════
# Step 2: ACR 推送
# ═══════════════════════════════════════════════════

def step_acr(ak_id, ak_secret):
    print("\n📦 [2/5] ACR 推送...")
    from alibabacloud_cr20181201.client import Client as CR
    from alibabacloud_cr20181201 import models as m

    cr = make_client(CR, f"cr.{REGION}.aliyuncs.com", ak_id, ak_secret)

    # 命名空间
    try:
        cr.create_namespace(m.CreateNamespaceRequest(
            instance_id="cri",
            namespace_name=ACR_NS,
            auto_create_repo=False,
            default_repo_type="PUBLIC",
        ))
        print(f"   ✅ 命名空间: {ACR_NS}")
    except Exception as e:
        if "exist" in str(e).lower():
            print(f"   ⚠️  命名空间已存在")
        else:
            print(f"   ❌ 命名空间创建失败: {e}")
            sys.exit(1)

    # 仓库
    try:
        cr.create_repository(m.CreateRepositoryRequest(
            instance_id="cri",
            repo_name=ACR_REPO,
            repo_namespace_name=ACR_NS,
            repo_type="PRIVATE",
            summary="AIHR",
            detail="AIHR FastAPI 应用",
        ))
        print(f"   ✅ 仓库: {ACR_NS}/{ACR_REPO}")
    except Exception as e:
        if "exist" in str(e).lower():
            print(f"   ⚠️  仓库已存在")
        else:
            print(f"   ❌ 仓库创建失败: {e}")
            sys.exit(1)

    # 登录 — 优先用固定密码，否则尝试临时 token
    acr_password = os.environ.get("ACR_PASSWORD", "").strip()
    if acr_password:
        # 固定密码方式（推荐，RAM 用户兼容）
        username = os.environ.get("ACR_USERNAME", "").strip()
        r = subprocess.run(
            f"docker login --username={username} --password={acr_password} {ACR_ENDPOINT}",
            shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    else:
        # 临时 token 方式（需要 GetAuthorizationToken 权限）
        try:
            token = cr.get_authorization_token(m.GetAuthorizationTokenRequest(instance_id="cri")).body
            if token.authorization_token:
                pwd = base64.b64decode(token.authorization_token).decode().split(":")[1]
                r = subprocess.run(
                    f"docker login --username={token.temp_username} --password={pwd} {ACR_ENDPOINT}",
                    shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace"
                )
            else:
                print("   ❌ 获取临时 token 失败（权限不足）")
                print("   💡 请设固定密码: export ACR_USERNAME=xxx ACR_PASSWORD=xxx")
                sys.exit(1)
        except Exception as e:
            print(f"   ❌ 获取临时 token 失败: {e}")
            print("   💡 请设固定密码: ACR 控制台 -> 访问凭证 -> 设置固定密码")
            sys.exit(1)
    if r.returncode != 0:
        print(f"❌ ACR 登录失败: {r.stderr}")
        sys.exit(1)
    print("   ✅ 已登录")

    # Tag + Push
    subprocess.run(["docker", "tag", "aihr:latest", IMAGE_TAG], check=True)
    print(f"   ⏳ 推送 {IMAGE_TAG} ...")
    r = subprocess.run(["docker", "push", IMAGE_TAG], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"❌ 推送失败:\n{r.stderr[-500:]}")
        sys.exit(1)
    print("   ✅ 推送完成")


# ═══════════════════════════════════════════════════
# Step 3: VPC + vSwitch
# ═══════════════════════════════════════════════════

def step_vpc(ak_id, ak_secret):
    print("\n📦 [3/5] VPC 网络...")
    from alibabacloud_vpc20160428.client import Client as VPC
    from alibabacloud_vpc20160428 import models as m

    vpc = make_client(VPC, f"vpc.{REGION}.aliyuncs.com", ak_id, ak_secret)

    # 查已有 VPC
    resp = vpc.describe_vpcs(m.DescribeVpcsRequest(vpc_name="aihr-vpc"))
    if resp.body.vpcs and resp.body.vpcs.vpc:
        vpc_id = resp.body.vpcs.vpc[0].vpc_id
        print(f"   ⚠️  复用 VPC: {vpc_id}")
    else:
        resp = vpc.create_vpc(m.CreateVpcRequest(
            cidr_block="172.16.0.0/12", vpc_name="aihr-vpc"))
        vpc_id = resp.body.vpc_id
        time.sleep(3)
        print(f"   ✅ VPC 已创建: {vpc_id}")

    # 查已有交换机
    resp = vpc.describe_v_switches(m.DescribeVSwitchesRequest(vpc_id=vpc_id))
    if resp.body.v_switches and resp.body.v_switches.v_switch:
        vswitch_id = resp.body.v_switches.v_switch[0].v_switch_id
        print(f"   ⚠️  复用交换机: {vswitch_id}")
    else:
        resp = vpc.create_v_switch(m.CreateVSwitchRequest(
            vpc_id=vpc_id, cidr_block="172.16.0.0/20",
            zone_id=NAS_ZONE, v_switch_name="aihr-vswitch"))
        vswitch_id = resp.body.v_switch_id
        print(f"   ✅ 交换机已创建: {vswitch_id}")

    return vpc_id, vswitch_id


# ═══════════════════════════════════════════════════
# Step 4: NAS 文件系统
# ═══════════════════════════════════════════════════

def step_nas(ak_id, ak_secret, vpc_id, vswitch_id):
    print("\n📦 [4/5] NAS 文件存储...")
    from alibabacloud_nas20170626.client import Client as NAS
    from alibabacloud_nas20170626 import models as m

    nas = make_client(NAS, f"nas.{REGION}.aliyuncs.com", ak_id, ak_secret)

    # 查已有文件系统
    fs_id = None
    resp = nas.describe_file_systems(m.DescribeFileSystemsRequest())
    for fs in (resp.body.file_systems.file_system or []):
        if (fs.description or "") == "aihr-data":
            fs_id = fs.file_system_id
            break

    if fs_id:
        print(f"   ⚠️  复用 NAS: {fs_id}")
    else:
        resp = nas.create_file_system(m.CreateFileSystemRequest(
            file_system_type="standard", storage_type="Capacity",
            zone_id=NAS_ZONE, protocol_type="NFS", description="aihr-data"))
        fs_id = resp.body.file_system_id
        print(f"   ✅ NAS 已创建: {fs_id}")

    # 挂载点
    mount_domain = None
    try:
        nas.create_mount_target(m.CreateMountTargetRequest(
            file_system_id=fs_id, network_type="Vpc",
            vpc_id=vpc_id, v_switch_id=vswitch_id,
            access_group_name="DEFAULT_VPC_GROUP_NAME"))
        print("   ⏳ 等待挂载点就绪 (15s)...")
        time.sleep(15)
    except Exception as e:
        if "exist" in str(e).lower():
            print("   ⚠️  挂载点已存在")
        else:
            raise

    resp = nas.describe_mount_targets(
        m.DescribeMountTargetsRequest(file_system_id=fs_id))
    for mt in (resp.body.mount_targets.mount_target or []):
        if mt.status == "Active":
            mount_domain = mt.mount_target_domain
            break
    if not mount_domain:
        mount_domain = f"{fs_id}-{NAS_ZONE}.nas.aliyuncs.com"
        print(f"   ⚠️  挂载点可能未就绪，使用: {mount_domain}")
    else:
        print(f"   ✅ 挂载点: {mount_domain}")

    return fs_id, mount_domain


# ═══════════════════════════════════════════════════
# Step 5: FC 函数
# ═══════════════════════════════════════════════════

def step_fc(ak_id, ak_secret, account_id, vpc_id, vswitch_id, mount_domain, app_env):
    print("\n📦 [5/5] FC 函数...")
    from alibabacloud_fc_open20210406.client import Client as FC
    from alibabacloud_fc_open20210406 import models as m

    fc = make_client(FC, f"{account_id}.{REGION}.fc.aliyuncs.com", ak_id, ak_secret)

    # 环境变量
    env_vars = {
        **app_env,
        "DATABASE_PATH": "/mnt/nas/data.db",
        "HOST": "0.0.0.0",
        "PORT": "8000",
    }

    # 服务（创建或复用）
    try:
        fc.create_service(m.CreateServiceRequest(
            service_name=FC_SVC,
            description="AIHR 智能招聘助手",
            internet_access=True,
            vpc_config=m.VPCConfig(
                vpc_id=vpc_id,
                v_switch_ids=[vswitch_id],
                security_group_id="",
            ),
            nas_config=m.NASConfig(
                user_id=10003,
                group_id=10003,
                mount_points=[m.NASConfigMountPoints(
                    server_addr=f"{mount_domain}:/",
                    mount_dir="/mnt/nas",
                )],
            ),
        ))
        print(f"   ✅ 服务: {FC_SVC}")
    except Exception as e:
        if "exist" in str(e).lower():
            print(f"   ⚠️  服务已存在")
        else:
            print(f"   ❌ 创建服务失败: {e}")
            sys.exit(1)

    # 函数（创建或更新）
    try:
        fc.create_function(m.CreateFunctionRequest(
            service_name=FC_SVC,
            function_name=FC_FN,
            description="AIHR FastAPI 应用",
            runtime="custom-container",
            handler="index.handler",
            memory_size=512,
            timeout=120,
            custom_container_config=m.CustomContainerConfig(
                image=IMAGE_TAG,
                port=8000,
            ),
            environment_variables=env_vars,
        ))
        print(f"   ✅ 函数: {FC_FN}")
    except Exception as e:
        if "exist" in str(e).lower():
            print("   ⚠️  函数已存在，更新中...")
            fc.update_function(m.UpdateFunctionRequest(
                service_name=FC_SVC,
                function_name=FC_FN,
                memory_size=512,
                timeout=120,
                custom_container_config=m.CustomContainerConfig(
                    image=IMAGE_TAG,
                    port=8000,
                ),
                environment_variables=env_vars,
            ))
            print(f"   ✅ 函数已更新")
        else:
            print(f"   ❌ 创建/更新函数失败: {e}")
            sys.exit(1)

    # HTTP 触发器
    try:
        fc.create_trigger(m.CreateTriggerRequest(
            service_name=FC_SVC,
            function_name=FC_FN,
            trigger_name="http",
            trigger_type="http",
            trigger_config=json.dumps({
                "authType": "anonymous",
                "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
            }),
        ))
        print("   ✅ HTTP 触发器")
    except Exception as e:
        if "exist" in str(e).lower():
            print("   ⚠️  HTTP 触发器已存在")
        else:
            print(f"   ⚠️  触发器: {e}")

    url = f"https://{account_id}.{REGION}.fc.aliyuncs.com/{FC_SVC}/{FC_FN}"
    return url


# ═══════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    print("[AIHR] 一键部署 -- 阿里云 FC Custom Container\n")

    # 0. 加载配置
    ak_id, ak_secret, app_env = load_config()
    account_id = get_account_id(ak_id, ak_secret)
    print(f"   [OK] AK 有效 | Account: {account_id}")

    # 1-5. 执行部署
    step_build()
    step_acr(ak_id, ak_secret)
    vpc_id, vswitch_id = step_vpc(ak_id, ak_secret)
    fs_id, mount_domain = step_nas(ak_id, ak_secret, vpc_id, vswitch_id)
    url = step_fc(ak_id, ak_secret, account_id, vpc_id, vswitch_id, mount_domain, app_env)

    # 结果
    print(f"\n{'=' * 60}")
    print(f"🎉 部署完成！")
    print(f"   {url}")
    print(f"")
    print(f"⚠️  冷启动 10-30 秒 | 后续更新只需 build + push")
    print(f"{'=' * 60}")
