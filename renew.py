import os
import sys
import asyncio
import requests
import random
import json
import logging
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from playwright_stealth import stealth_async  # 核心组件：强力隐匿指纹插件

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- 环境变量配置 ---
DP_EMAIL = os.getenv("DP_EMAIL")
DP_PASSWORD = os.getenv("DP_PASSWORD")
DP_COOKIES = os.getenv("DP_COOKIES")      # 新增：直接注入有效 Cookie 格式的 JSON 字符串绕过鉴权
PROXY_SERVER = os.getenv("PROXY_SERVER")  # 新增：上游代理配置 (格式示例 http://user:pass@ip:port)

# 通知配置 (Bark & Telegram)
BARK_KEY = os.getenv("BARK_KEY")
BARK_SERVER = os.getenv("BARK_SERVER", "https://api.day.app")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

# --- 常量定义 ---
LOGIN_URL = "https://dash.domain.digitalplat.org/auth/login"
DOMAINS_URL = "https://dash.domain.digitalplat.org/panel/main?page=%2Fpanel%2Fdomains"
TIMEOUTS = {
    "page_load": 60000,
    "element_wait": 30000,
    "navigation": 60000,
    "login_wait": 180000
}

def validate_config():
    """检查必要的运行凭据"""
    # 存在有效 Cookie 时可无需显式传递密码
    if not DP_COOKIES and (not DP_EMAIL or not DP_PASSWORD):
        logger.error("配置错误: 缺少账户登录凭证 (需配置 DP_EMAIL/DP_PASSWORD 或直接提供 DP_COOKIES)。")
        send_notification("DigitalPlat 配置异常", "缺少底层必要身份环境变量配置，停止运行。")
        sys.exit(1)

def send_notification(title, body, level="active"):
    """统一通知路由 (Bark + Telegram)"""
    logger.info(f"触发通知推送流: {title}")
    
    # 1. 投递 Bark
    if BARK_KEY:
        try:
            api_url = f"{BARK_SERVER.rstrip('/')}/{BARK_KEY}"
            payload = {
                "title": title,
                "body": body,
                "group": "DigitalPlat Renew",
                "level": level
            }
            requests.post(api_url, json=payload, timeout=10)
        except Exception as e:
            logger.error(f"Bark 接口投递失败: {e}")

    # 2. 投递 Telegram
    if TG_BOT_TOKEN and TG_CHAT_ID:
        try:
            tg_url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            text_content = f"*{title}*\n\n{body}"
            payload = {
                "chat_id": TG_CHAT_ID,
                "text": text_content,
                "parse_mode": "Markdown"
            }
            requests.post(tg_url, json=payload, timeout=10)
        except Exception as e:
            logger.error(f"Telegram 接口投递失败: {e}")

def save_results(renewed_domains, failed_domains):
    """序列化本地运行快照"""
    results = {
        "timestamp": datetime.now().isoformat(),
        "renewed_count": len(renewed_domains),
        "failed_count": len(failed_domains),
        "renewed_domains": renewed_domains,
        "failed_domains": failed_domains
    }
    try:
        with open("renewal_results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"构建本地快照结果失败: {e}")

async def simulate_human_behavior(page):
    """模拟真实环境的物理输入扰动"""
    await page.mouse.move(random.randint(100, 500), random.randint(100, 500))
    await asyncio.sleep(random.uniform(0.5, 2))

async def setup_browser(playwright):
    """构建强化底层反爬防御的无头运行上下文"""
    # 变更为 Chromium 内核以配合 stealth 获得最佳隐匿兼容性
    browser = await playwright.chromium.launch(
        headless=True,  # 本地调试排障时可临时设为 False 显式渲染
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-gpu',
            '--disable-infobars',
            '--window-position=0,0',
            '--ignore-certificate-errors'
        ]
    )
    
    # 解析并挂载上游静态/住宅代理通道
    proxy_config = {"server": PROXY_SERVER} if PROXY_SERVER else None
    if proxy_config:
        logger.info(f"已动态注入代理网关拓扑: {PROXY_SERVER.split('@')[-1] if '@' in PROXY_SERVER else PROXY_SERVER}")

    context = await browser.new_context(
        proxy=proxy_config,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080}
    )
    return browser, context

async def wait_for_cloudflare(page):
    """Cloudflare 5秒盾/质询验证器智能缓冲监听流"""
    logger.info("挂载反检测探针，扫描当前页面防护载荷...")
    await asyncio.sleep(random.uniform(4, 7))
    
    for i in range(12):  # 提供充裕的等待上限周期 (约60秒)
        content = await page.content()
        # 捕捉特征码判定是否处于网关拦截态
        if any(keyword in content for keyword in ["cf-challenge", "Checking your browser", "Verify you are human", "Cloudflare"]):
            logger.info(f"Cloudflare 动态计算盾正在解析换签... 缓冲周期等待中 ({i+1}/12)")
            await simulate_human_behavior(page)
            await asyncio.sleep(5)
        else:
            logger.info("目标页面质询解除完毕或未触发高敏风控")
            break

async def login(page, context):
    """双通道鉴权驱动：支持本地会话直接注入及常规键入回退"""
    # 方案 A: 检测到已授权外部会话，进行直连注入
    if DP_COOKIES:
        try:
            logger.info("捕获免密状态字 DP_COOKIES，尝试执行底层 Session 写入绕过网关...")
            cookies = json.loads(DP_COOKIES)
            await context.add_cookies(cookies)
            await page.goto(DOMAINS_URL, wait_until="networkidle", timeout=TIMEOUTS["page_load"])
            if "/panel/" in page.url:
                logger.info("底层 Cookie 会话重载成功，穿透直接抵达面板！")
                return
            else:
                logger.warning("Cookie 会话令牌已失效或发生降级，准备回退进入原生键入通道...")
        except Exception as e:
            logger.error(f"反序列化及写入会话时触发异常: {e}，正在重试标准登录流程...")

    # 方案 B: 执行常规键入流程
    logger.info("发起登录入口握手请求...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=TIMEOUTS["page_load"])
    
    # 过盾等待缓冲拦截处理
    await wait_for_cloudflare(page)

    # 尝试提取关键输入载体
    try:
        email_input = page.locator("input[name='email']")
        await email_input.wait_for(state="visible", timeout=TIMEOUTS["element_wait"])
    except PlaywrightTimeoutError:
        error_msg = "握手超时: 无法穿透动态网关验证或 DOM 渲染停滞"
        logger.error(error_msg)
        await page.screenshot(path="cf_block_timeout.png")
        send_notification("DigitalPlat 登录熔断", "无法穿透宿主 Cloudflare 动态质询验证，请调取本地截图溯源。")
        raise Exception(error_msg)

    # 注入抖动时间间隔还原人工输入习惯
    await email_input.type(DP_EMAIL, delay=random.randint(50, 150))
    await page.type("input[name='password']", DP_PASSWORD, delay=random.randint(50, 150))
    await simulate_human_behavior(page)
    
    async with page.expect_navigation(wait_until="networkidle", timeout=TIMEOUTS["navigation"]):
        await page.click("button[type='submit']")

    # 校验最终鉴权重定向态
    if "/panel/main" not in page.url:
        await page.screenshot(path="login_failed.png")
        raise Exception("登录断言失败: 未发现面板终态路由跳转，可能因为凭证失真或网关次级拦截。")
    
    logger.info("常规鉴权验证通过！")

async def process_domain(page, domain_name, domain_url_path, base_url):
    """单体域名事务续期流水线处理"""
    try:
        full_url = base_url + domain_url_path.lstrip('/')
        logger.info(f"读取实例状态: {domain_name}")
        await page.goto(full_url, wait_until="networkidle", timeout=TIMEOUTS["navigation"])

        renew_link = page.locator("a[href*='renewdomain']")
        if await renew_link.count() == 0:
            logger.info(f"{domain_name}: 生命周期充足或未开放续期通道")
            return None, None

        logger.info(f"{domain_name}: 探测到高优操作节点，下发续订动作流...")
        async with page.expect_navigation(timeout=TIMEOUTS["navigation"]):
            await renew_link.click()

        # 兼容订单结算入口多语言适配词态
        btn = page.locator("button:has-text('Order Now'), button:has-text('Continue')").first
        if await btn.count() > 0:
            async with page.expect_navigation(timeout=TIMEOUTS["navigation"]):
                await btn.click()
            
            # TOS 检查规则校验
            tos = page.locator("input[name='accepttos']")
            if await tos.count() > 0:
                await tos.check()
            
            # 驱动最终网关结算
            checkout = page.locator("button#checkout")
            if await checkout.count() > 0:
                async with page.expect_navigation(timeout=TIMEOUTS["navigation"]):
                    await checkout.click()
                
                content = await page.inner_text("body")
                if "Order Confirmation" in content or "successfully" in content.lower():
                    logger.info(f"[{domain_name}] 续期事物链核销完毕！")
                    return True, None
                else:
                    return False, f"{domain_name} (末端账单核销未产生成功态标识)"
            return False, f"{domain_name} (未检测到可执行的 checkout 结算节点)"
        return False, f"{domain_name} (流程入口确实 Order Now/Continue 动作按钮)"

    except Exception as e:
        logger.error(f"调度实例 {domain_name} 时发生系统异常: {e}")
        return False, f"{domain_name} (运行时崩溃: {str(e)})"

async def main():
    validate_config()
    renewed = []
    failed = []

    async with async_playwright() as p:
        browser, context = await setup_browser(p)
        page = await context.new_page()
        
        # 绝对防线：执行隐匿扩展套件完整注册注入
        await stealth_async(page)
        
        try:
            await login(page, context)
            
            await page.goto(DOMAINS_URL, wait_until="networkidle")
            await page.wait_for_selector("table.table-domains", timeout=TIMEOUTS["element_wait"])
            
            rows = await page.locator("table.table-domains tbody tr").all()
            base_url = "https://dash.domain.digitalplat.org/"
            
            logger.info(f"全局扫描任务已加载，共寻获挂载条目: {len(rows)} 项")
            
            for row in rows:
                onclick = await row.get_attribute("onclick")
                if onclick:
                    path = onclick.split("'")[1]
                    name = await row.locator("td:nth-child(1)").inner_text()
                    
                    is_success, error = await process_domain(page, name.strip(), path, base_url)
                    if is_success:
                        renewed.append(name.strip())
                    elif error:
                        failed.append(error)
                    
                    # 核验完毕重置环境池，返回控制总线准备下一个轮次
                    await page.goto(DOMAINS_URL, wait_until="networkidle")

            # 构建报告载荷及消息队列下发
            if renewed or failed:
                msg = ""
                if renewed:
                    msg += f"✅ 成功完成续期: {len(renewed)} 个\n" + "\n".join(renewed) + "\n\n"
                if failed:
                    msg += f"❌ 续期遇到错误: {len(failed)} 个\n" + "\n".join(failed)
                send_notification("DigitalPlat 自动续订报告", msg.strip())
            else:
                logger.info("扫描队列清空，无域名到达当前到期阈值")
                send_notification("DigitalPlat 巡检结果", "全部域名实例运行正常，未发生续费操作。", level="passive")

            save_results(renewed, failed)

        except Exception as e:
            logger.critical(f"容器主进程捕捉未处理级中断: {e}")
            send_notification("DigitalPlat 调度流崩溃", f"运行时异常: {str(e)}")
            sys.exit(1)
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
