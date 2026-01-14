"""
报告生成器模块
负责生成各种格式的分析报告
"""

import asyncio
import base64
from datetime import datetime
from pathlib import Path

import aiohttp

from astrbot.api import logger

from ..visualization.activity_charts import ActivityVisualizer
from .templates import HTMLTemplates


class ReportGenerator:
    """报告生成器"""

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.activity_visualizer = ActivityVisualizer()
        self.html_templates = HTMLTemplates(config_manager)  # 实例化HTML模板管理器

    async def generate_image_report(
        self, analysis_result: dict, group_id: str, html_render_func
    ) -> str | None:
        """生成图片格式的分析报告"""
        try:
            # 准备渲染数据
            render_payload = await self._prepare_render_data(analysis_result)

            # 先渲染HTML模板（使用异步方法）
            image_template = await self.html_templates.get_image_template_async()
            html_content = self._render_html_template(image_template, render_payload)

            # 检查HTML内容是否有效
            if not html_content:
                logger.error("图片报告HTML渲染失败：返回空内容")
                return None

            logger.info(f"图片报告HTML渲染完成，长度: {len(html_content)} 字符")

            # 使用AstrBot内置的HTML渲染服务（传递渲染后的HTML）
            # 使用兼容的图片生成选项（基于NetworkRenderStrategy的默认设置）
            image_options = {
                "full_page": True,
                "type": "jpeg",  # 使用默认的jpeg格式提高兼容性
                "quality": 95,  # 设置合理的质量
            }
            image_url = await html_render_func(
                html_content,  # 渲染后的HTML内容
                {},  # 空数据字典，因为数据已包含在HTML中
                True,  # return_url=True，返回URL而不是下载文件
                image_options,
            )

            logger.info(f"图片生成成功: {image_url}")
            return image_url

        except Exception as e:
            logger.error(f"生成图片报告失败: {e}", exc_info=True)
            # 尝试使用更简单的选项作为后备方案
            try:
                logger.info("尝试使用低质量选项重新生成...")
                simple_options = {
                    "full_page": True,
                    "type": "jpeg",
                    "quality": 70,  # 降低质量以提高兼容性
                }
                image_url = await html_render_func(
                    html_content,  # 使用已渲染的HTML
                    {},  # 空数据字典
                    True,
                    simple_options,
                )
                logger.info(f"使用低质量选项生成成功: {image_url}")
                return image_url
            except Exception as fallback_e:
                logger.error(f"后备低质量方案也失败: {fallback_e}")
                return None

    async def generate_pdf_report(
        self, analysis_result: dict, group_id: str
    ) -> str | None:
        """生成PDF格式的分析报告"""
        try:
            # 确保输出目录存在（使用 asyncio.to_thread 避免阻塞）
            output_dir = Path(self.config_manager.get_pdf_output_dir())
            await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)

            # 生成文件名
            current_date = datetime.now().strftime("%Y%m%d")
            filename = self.config_manager.get_pdf_filename_format().format(
                group_id=group_id, date=current_date
            )
            pdf_path = output_dir / filename

            # 准备渲染数据
            render_data = await self._prepare_render_data(analysis_result)
            logger.info(f"PDF 渲染数据准备完成，包含 {len(render_data)} 个字段")

            # 生成 HTML 内容（使用异步方法）
            pdf_template = await self.html_templates.get_pdf_template_async()
            html_content = self._render_html_template(pdf_template, render_data)

            # 检查HTML内容是否有效
            if not html_content:
                logger.error("PDF报告HTML渲染失败：返回空内容")
                return None

            logger.info(f"HTML 内容生成完成，长度: {len(html_content)} 字符")

            # 转换为 PDF
            success = await self._html_to_pdf(html_content, str(pdf_path))

            if success:
                return str(pdf_path.absolute())
            else:
                return None

        except Exception as e:
            logger.error(f"生成 PDF 报告失败: {e}")
            return None

    def generate_text_report(self, analysis_result: dict) -> str:
        """生成文本格式的分析报告"""
        stats = analysis_result["statistics"]
        topics = analysis_result["topics"]
        user_titles = analysis_result["user_titles"]

        report = f"""
🎯 群聊日常分析报告
📅 {datetime.now().strftime("%Y年%m月%d日")}

📊 基础统计
• 消息总数: {stats.message_count}
• 参与人数: {stats.participant_count}
• 总字符数: {stats.total_characters}
• 表情数量: {stats.emoji_count}
• 最活跃时段: {stats.most_active_period}

💬 热门话题
"""

        max_topics = self.config_manager.get_max_topics()
        for i, topic in enumerate(topics[:max_topics], 1):
            contributors_str = "、".join(topic.contributors)
            report += f"{i}. {topic.topic}\n"
            report += f"   参与者: {contributors_str}\n"
            report += f"   {topic.detail}\n\n"

        report += "🏆 群友称号\n"
        max_user_titles = self.config_manager.get_max_user_titles()
        for title in user_titles[:max_user_titles]:
            report += f"• {title.name} - {title.title} ({title.mbti})\n"
            report += f"  {title.reason}\n\n"

        report += "💬 群圣经\n"
        max_golden_quotes = self.config_manager.get_max_golden_quotes()
        for i, quote in enumerate(stats.golden_quotes[:max_golden_quotes], 1):
            report += f'{i}. "{quote.content}" —— {quote.sender}\n'
            report += f"   {quote.reason}\n\n"

        return report

    async def _prepare_render_data(self, analysis_result: dict) -> dict:
        """准备渲染数据"""
        stats = analysis_result["statistics"]
        topics = analysis_result["topics"]
        user_titles = analysis_result["user_titles"]
        activity_viz = stats.activity_visualization

        # 使用Jinja2模板构建话题HTML（批量渲染）
        max_topics = self.config_manager.get_max_topics()
        topics_list = []
        for i, topic in enumerate(topics[:max_topics], 1):
            topics_list.append(
                {
                    "index": i,
                    "topic": topic,
                    "contributors": "、".join(topic.contributors),
                }
            )

        topics_html = self.html_templates.render_template(
            "topic_item.html", topics=topics_list
        )
        if topics_html is not None:
            logger.info(f"话题HTML生成完成，长度: {len(topics_html)}")
        else:
            logger.info("话题模板不存在，跳过话题部分渲染")

        # 使用Jinja2模板构建用户称号HTML（批量渲染，包含头像）
        max_user_titles = self.config_manager.get_max_user_titles()
        titles_list = []
        for title in user_titles[:max_user_titles]:
            # 获取用户头像
            avatar_data = await self._get_user_avatar(str(title.qq))
            title_data = {
                "name": title.name,
                "title": title.title,
                "mbti": title.mbti,
                "reason": title.reason,
                "avatar_data": avatar_data,
            }
            titles_list.append(title_data)

        titles_html = self.html_templates.render_template(
            "user_title_item.html", titles=titles_list
        )
        if titles_html is not None:
            logger.info(f"用户称号HTML生成完成，长度: {len(titles_html)}")
        else:
            logger.info("用户称号模板不存在，跳过用户称号部分渲染")

        # 使用Jinja2模板构建金句HTML（批量渲染）
        max_golden_quotes = self.config_manager.get_max_golden_quotes()
        quotes_list = []
        for quote in stats.golden_quotes[:max_golden_quotes]:
            avatar_url = (
                await self._get_user_avatar(str(quote.qq)) if quote.qq else None
            )
            quotes_list.append(
                {
                    "content": quote.content,
                    "sender": quote.sender,
                    "reason": quote.reason,
                    "avatar_url": avatar_url,
                }
            )

        quotes_html = self.html_templates.render_template(
            "quote_item.html", quotes=quotes_list
        )
        if quotes_html is not None:
            logger.info(f"金句HTML生成完成，长度: {len(quotes_html)}")
        else:
            logger.info("金句模板不存在，跳过金句部分渲染")

        # 生成活跃度可视化HTML
        chart_data = self.activity_visualizer.get_hourly_chart_data(
            activity_viz.hourly_activity
        )
        hourly_chart_html = self.html_templates.render_template(
            "activity_chart.html", chart_data=chart_data
        )
        if hourly_chart_html is not None:
            logger.info(f"活跃度图表HTML生成完成，长度: {len(hourly_chart_html)}")
        else:
            logger.info("活跃度图表模板不存在，跳过活跃度图表部分渲染")

        # 准备最终渲染数据
        render_data = {
            "current_date": datetime.now().strftime("%Y年%m月%d日"),
            "current_datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message_count": stats.message_count,
            "participant_count": stats.participant_count,
            "total_characters": stats.total_characters,
            "emoji_count": stats.emoji_count,
            "most_active_period": stats.most_active_period,
            "total_tokens": stats.token_usage.total_tokens
            if stats.token_usage.total_tokens
            else 0,
            "prompt_tokens": stats.token_usage.prompt_tokens
            if stats.token_usage.prompt_tokens
            else 0,
            "completion_tokens": stats.token_usage.completion_tokens
            if stats.token_usage.completion_tokens
            else 0,
        }
        
        # 只添加成功渲染的模板部分
        if topics_html is not None:
            render_data["topics_html"] = topics_html
        if titles_html is not None:
            render_data["titles_html"] = titles_html
        if quotes_html is not None:
            render_data["quotes_html"] = quotes_html
        if hourly_chart_html is not None:
            render_data["hourly_chart_html"] = hourly_chart_html

        logger.info(f"渲染数据准备完成，包含 {len(render_data)} 个字段")
        return render_data

    def _render_html_template(self, template: str, data: dict) -> str:
        """HTML模板渲染，使用 {{key}} 占位符格式

        Args:
            template: HTML模板字符串
            data: 渲染数据字典
        """
        result = template

        for key, value in data.items():
            # 统一使用双大括号格式 {{key}}
            placeholder = "{{" + key + "}}"
            # 如果值为 None，替换为空字符串（表示该部分不渲染）
            if value is None:
                result = result.replace(placeholder, "")
            else:
                result = result.replace(placeholder, str(value))

        # 检查是否还有未替换的占位符（排除可选部分）
        import re

        remaining_placeholders = re.findall(r"\{\{[^}]+\}\}", result)
        # 过滤掉已知的可选占位符（这些可能因为模板不存在而不被替换）
        optional_placeholders = ["topics_html", "titles_html", "quotes_html", "hourly_chart_html"]
        important_placeholders = [
            p for p in remaining_placeholders 
            if p.strip("{}") not in optional_placeholders
        ]
        
        if important_placeholders:
            logger.warning(
                f"未替换的重要占位符 ({len(important_placeholders)}个): {important_placeholders[:10]}"
            )
        elif remaining_placeholders:
            logger.debug(
                f"未替换的可选占位符（模板可能不存在）: {remaining_placeholders}"
            )

        return result

    async def _get_user_avatar(self, user_id: str) -> str | None:
        """获取用户头像的base64编码"""
        try:
            avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"
            async with aiohttp.ClientSession() as client:
                response = await client.get(avatar_url)
                response.raise_for_status()
                avatar_data = await response.read()
                # 转换为base64编码
                avatar_base64 = base64.b64encode(avatar_data).decode("utf-8")
                return f"data:image/jpeg;base64,{avatar_base64}"
        except Exception as e:
            logger.error(f"获取用户头像失败 {user_id}: {e}")
            return None

    async def _html_to_pdf(self, html_content: str, output_path: str) -> bool:
        """将 HTML 内容转换为 PDF 文件"""
        try:
            # 确保 pyppeteer 可用
            if not self.config_manager.pyppeteer_available:
                logger.error("pyppeteer 不可用，无法生成 PDF")
                return False

            # 动态导入 pyppeteer
            import os
            import sys

            import pyppeteer
            from pyppeteer import launch

            # 尝试启动浏览器，如果 Chromium 不存在会自动下载
            logger.info("启动浏览器进行 PDF 转换")

            # 配置浏览器启动参数，解决Docker环境中的沙盒问题
            launch_options = {
                "headless": True,
                "args": [
                    "--no-sandbox",  # Docker环境必需 - 禁用沙盒
                    "--disable-setuid-sandbox",  # Docker环境必需 - 禁用setuid沙盒
                    "--disable-dev-shm-usage",  # 避免共享内存问题
                    "--disable-gpu",  # 禁用GPU加速
                    "--no-first-run",
                    "--disable-extensions",
                    "--disable-default-apps",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                    "--disable-features=TranslateUI",
                    "--disable-ipc-flooding-protection",
                    "--disable-background-networking",
                    "--enable-features=NetworkService,NetworkServiceInProcess",
                    "--force-color-profile=srgb",
                    "--metrics-recording-only",
                    "--disable-breakpad",
                    "--disable-component-extensions-with-background-pages",
                    "--disable-features=Translate,BackForwardCache,AcceptCHFrame,AvoidUnnecessaryBeforeUnloadCheckSync",
                    "--enable-automation",
                    "--password-store=basic",
                    "--use-mock-keychain",
                    "--export-tagged-pdf",
                    "--disable-web-security",
                    "--disable-features=VizDisplayCompositor",
                    "--disable-blink-features=AutomationControlled",  # 隐藏自动化特征
                ],
            }

            # 检测系统 Chrome/Chromium 路径
            chrome_paths = []

            if sys.platform.startswith("win"):
                # Windows 系统 Chrome 安装路径
                username = os.environ.get("USERNAME", "")
                chrome_paths = [
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                    rf"C:\Users\{username}\AppData\Local\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files\Chromium\Application\chrome.exe",
                ]
            elif sys.platform.startswith("linux"):
                # Linux 系统 Chrome/Chromium 路径
                chrome_paths = [
                    "/usr/bin/google-chrome",
                    "/usr/bin/google-chrome-stable",
                    "/usr/bin/chromium",
                    "/usr/bin/chromium-browser",
                    "/snap/bin/chromium",
                    "/usr/bin/chromium-freeworld",
                ]
            elif sys.platform.startswith("darwin"):
                # macOS 系统 Chrome 路径
                chrome_paths = [
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Chromium.app/Contents/MacOS/Chromium",
                ]

            # 查找可用的浏览器
            found_browser = False
            for chrome_path in chrome_paths:
                if Path(chrome_path).exists():
                    launch_options["executablePath"] = chrome_path
                    logger.info(f"使用系统浏览器: {chrome_path}")
                    found_browser = True
                    break

            if not found_browser:
                logger.info("未找到系统浏览器，将使用 pyppeteer 默认下载的 Chromium")
                # 先尝试确保 Chromium 已下载
                try:
                    from pyppeteer import browser, launcher

                    launcher_instance = launcher.Launcher(
                        headless=True,
                        args=[
                            "--no-sandbox",
                            "--disable-setuid-sandbox",
                            "--disable-dev-shm-usage",
                        ],
                    )
                    await launcher_instance._get_chromium_revision()
                    await launcher_instance._download_chromium()
                    chromium_path = pyppeteer.executablePath()
                    launch_options["executablePath"] = chromium_path
                    logger.info(f"使用 pyppeteer 下载的 Chromium: {chromium_path}")
                except Exception as pre_download_err:
                    logger.warning(
                        f"预下载 Chromium 失败，继续尝试直接启动: {pre_download_err}"
                    )

            # 尝试启动浏览器
            try:
                logger.info("正在启动浏览器...")
                browser = await launch(**launch_options)
                logger.info("浏览器启动成功")
            except Exception as e:
                logger.error(f"浏览器启动失败: {e}", exc_info=True)
                return False

            try:
                # 创建新页面，设置更合理的超时时间
                page = await browser.newPage()

                # 设置页面视口，减少内存占用
                await page.setViewport(
                    {
                        "width": 1024,
                        "height": 768,
                        "deviceScaleFactor": 1,
                        "isMobile": False,
                        "hasTouch": False,
                        "isLandscape": False,
                    }
                )

                # 设置页面内容，使用更安全的加载方式
                logger.info("开始设置页面内容...")
                await page.setContent(
                    html_content, {"waitUntil": "domcontentloaded", "timeout": 30000}
                )

                # 等待页面基本加载完成，但不要太长时间
                try:
                    await page.waitForSelector("body", {"timeout": 5000})
                    logger.info("页面基本加载完成")
                except Exception:
                    logger.warning("等待页面加载超时，继续执行")

                # 减少等待时间，避免内存累积
                await asyncio.sleep(1)

                # 导出 PDF，使用更保守的设置
                logger.info("开始生成PDF...")
                pdf_options = {
                    "path": output_path,
                    "format": "A4",
                    "printBackground": True,
                    "margin": {
                        "top": "10mm",
                        "right": "10mm",
                        "bottom": "10mm",
                        "left": "10mm",
                    },
                    "scale": 0.8,
                    "displayHeaderFooter": False,
                    "preferCSSPageSize": True,
                    "timeout": 60000,  # 增加PDF生成超时时间到60秒
                }

                await page.pdf(pdf_options)
                logger.info(f"PDF 生成成功: {output_path}")
                return True

            except Exception as e:
                logger.error(f"PDF生成过程中出错: {e}")
                return False

            finally:
                # 确保浏览器被正确关闭
                if browser:
                    try:
                        logger.info("正在关闭浏览器...")
                        # 先关闭所有页面
                        pages = await browser.pages()
                        for page in pages:
                            try:
                                await page.close()
                            except Exception as close_err:
                                logger.debug(f"关闭页面时忽略的异常: {close_err}")

                        # 等待一小段时间让资源释放
                        await asyncio.sleep(0.5)

                        # 关闭浏览器
                        await browser.close()
                        logger.info("浏览器已关闭")
                    except Exception as e:
                        logger.warning(f"关闭浏览器时出错: {e}")
                        # 强制清理
                        try:
                            await browser.disconnect()
                        except Exception as disc_err:
                            logger.debug(f"断开浏览器连接时忽略的异常: {disc_err}")

        except Exception as e:
            error_msg = str(e)
            if "Chromium downloadable not found" in error_msg:
                logger.error("Chromium 下载失败，建议安装系统 Chrome/Chromium")
                logger.info(
                    "💡 Linux 系统建议: sudo apt-get install chromium-browser 或 sudo yum install chromium"
                )
            elif "No usable sandbox" in error_msg:
                logger.error("沙盒权限问题，已尝试禁用沙盒")
            elif "Connection refused" in error_msg or "connect" in error_msg.lower():
                logger.error("浏览器连接失败，请检查系统资源或尝试重启")
            elif "executablePath" in error_msg and "not found" in error_msg:
                logger.error("未找到系统浏览器，请安装 Chrome 或 Chromium")
                logger.info(
                    "💡 安装建议: sudo apt-get install chromium-browser (Ubuntu/Debian) 或 sudo yum install chromium (CentOS/RHEL)"
                )
            elif "Browser closed unexpectedly" in error_msg:
                logger.error("浏览器意外关闭，可能是由于内存不足或系统资源限制")
                logger.info("💡 建议: 检查系统内存，或重启 AstrBot 后重试")
                logger.info("💡 如果问题持续，可以尝试以下解决方案:")
                logger.info("   1. 增加系统交换空间")
                logger.info("   2. 使用更简单的浏览器启动参数")
                logger.info("   3. 考虑使用其他 PDF 生成方案")
            else:
                logger.error(f"HTML 转 PDF 失败: {e}")
                logger.info(
                    "💡 可以尝试使用 /安装PDF 命令重新安装依赖，或检查系统日志获取更多信息"
                )
            return False
