import asyncio
import re
import logging
import os
import sys
# Windows 用 msvcrt，Linux/macOS 用 fcntl（自动适配）
try:
    import fcntl
except ImportError:
    import msvcrt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.error import RetryAfter, TimedOut
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 机器人配置
TOKEN = os.getenv("TOKEN")
CHANNEL_IDS = ['@yunpanNB', '@ammmziyuan']
SPECIFIC_CHANNELS = {
    'quark': '@yunpanquark', 'baidu': '@yunpanbaidu',
    'uc': '@pxyunpanuc', 'xunlei': '@pxyunpanxunlei'
}

# Token 校验
if not TOKEN:
    raise ValueError("❌ 未配置 TOKEN！创建 .env 文件添加 TOKEN=xxx")

# 用户数据存储
user_posts = {}
user_states = {}

class SingleInstanceLock:
    def __init__(self, lock_file_path="bot_instance.lock"):
        self.lock_file_path = lock_file_path
        self.lock_file = None
        self.is_locked = False

    def acquire(self):
        """获取锁，失败则抛出异常"""
        try:
            # 打开文件（不存在则创建）
            self.lock_file = open(self.lock_file_path, 'w')
            if sys.platform.startswith("win"):
                # Windows：用 msvcrt 锁定文件
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)  # 非阻塞锁定
            else:
                # Linux/macOS：用 fcntl 锁定文件
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.is_locked = True
            return True
        except (BlockingIOError, PermissionError):
            # 锁已被占用
            if self.lock_file:
                self.lock_file.close()
                self.lock_file = None
            return False

    def release(self):
        """释放锁"""
        if self.is_locked and self.lock_file:
            try:
                if sys.platform.startswith("win"):
                    msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self.lock_file.close()
                self.lock_file = None
                self.is_locked = False
                # 删除锁文件
                if os.path.exists(self.lock_file_path):
                    os.remove(self.lock_file_path)

# PostManager 类（保持不变，完整复制之前的代码）
class PostManager:
    def __init__(self):
        self.post_template = {
            'name': '',
            'description': '',
            'links': [],
            'size': '',
            'tags': ''
        }

    def format_links(self, links_text):
        links = links_text.split('\n')
        formatted_links = []
        for link in links:
            link = link.strip()
            if not link:
                continue
            if link.startswith("链接："):
                formatted_links.append(link)
            elif re.match(r"^(夸克|百度|UC|迅雷)：", link):
                actual_link = re.search(r"：\s*(https?://.+)", link)
                if actual_link:
                    formatted_links.append(f"链接：{actual_link.group(1)}")
                else:
                    formatted_links.append(f"链接：{link}")
            else:
                formatted_links.append(f"链接：{link}")
        if not formatted_links:
            formatted_links.append("链接：https://pan.quark.cn/s/3c07afa156f3")
        return '\n'.join(formatted_links)

    def remove_duplicate_links(self, caption):
        lines = caption.split('\n')
        processed_lines = []
        seen_links = set()
        for line in lines:
            if line.startswith("链接："):
                link_url = line[3:].strip()
                if link_url not in seen_links:
                    seen_links.add(link_url)
                    processed_lines.append(line)
            else:
                processed_lines.append(line)
        return '\n'.join(processed_lines)

    def identify_link_types(self, links):
        link_types = set()
        unrecognized_links = []
        if isinstance(links, str):
            links = [links]
        for link in links:
            if link.startswith("链接："):
                url = link[3:].strip()
            else:
                url = link.strip()
            if 'pan.quark.cn' in url:
                link_types.add('quark')
            elif 'pan.baidu.com' in url:
                link_types.add('baidu')
            elif 'drive.uc.cn' in url:
                link_types.add('uc')
            elif 'pan.xunlei.com' in url:
                link_types.add('xunlei')
            else:
                unrecognized_links.append(url)
        return link_types

    def get_channels_for_each_link(self, links):
        link_channel_mapping = []
        if isinstance(links, str):
            links = [links]
        for link in links:
            if link.startswith("链接："):
                url = link[3:].strip()
            else:
                url = link.strip()
            target_channels = list(CHANNEL_IDS)
            if 'pan.quark.cn' in url:
                target_channels.append('@yunpanquark')
            elif 'pan.baidu.com' in url:
                target_channels.append('@yunpanbaidu')
            elif 'drive.uc.cn' in url:
                target_channels.append('@pxyunpanuc')
            elif 'pan.xunlei.com' in url:
                target_channels.append('@pxyunpanxunlei')
            link_channel_mapping.append({
                'link': url,
                'channels': target_channels
            })
        return link_channel_mapping

    def get_target_channels(self, links):
        link_types = self.identify_link_types(links)
        if not link_types:
            return CHANNEL_IDS
        target_channels = set()
        target_channels.update(CHANNEL_IDS)
        for link_type in link_types:
            if link_type in SPECIFIC_CHANNELS:
                target_channels.add(SPECIFIC_CHANNELS[link_type])
        return list(target_channels)

    def create_channel_specific_caption(self, original_caption, link_type):
        lines = original_caption.split('\n')
        filtered_lines = []
        keep_link = False
        for line in lines:
            if line.startswith("链接："):
                url = line[3:].strip()
                if link_type == 'quark' and 'pan.quark.cn' in url:
                    keep_link = True
                elif link_type == 'baidu' and 'pan.baidu.com' in url:
                    keep_link = True
                elif link_type == 'uc' and 'drive.uc.cn' in url:
                    keep_link = True
                elif link_type == 'xunlei' and 'pan.xunlei.com' in url:
                    keep_link = True
                else:
                    keep_link = False
                if keep_link:
                    filtered_lines.append(line)
            else:
                filtered_lines.append(line)
        return '\n'.join(filtered_lines)

    def detect_ad_content(self, caption):
        ad_keywords = ['兼职', '招聘', '游戏代练', '刷单', '刷钻']
        desc_match = re.search(r"描述：\s*(.+?)(?=\n|$)", caption)
        if desc_match:
            description = desc_match.group(1)
            for keyword in ad_keywords:
                if keyword in description:
                    return True
        link_matches = re.findall(r"链接：\s*(https?://[^\s]+)", caption)
        for link in link_matches:
            if not re.match(r"https?://(pan\.quark\.cn|pan\.baidu\.com|drive\.uc\.cn|pan\.xunlei\.com)/", link):
                suspicious_patterns = [
                    r"taobao\.com", r"tmall\.com", r"jd\.com",
                    r"wechat", r"wx\.qq\.com", r"alipay\.com"
                ]
                for pattern in suspicious_patterns:
                    if re.search(pattern, link):
                        return True
        return False

    def strict_mode_parse(self, caption):
        parsed_data = {
            'name': '',
            'description': '',
            'links': [],
            'size': '',
            'tags': ''
        }
        name_match = re.search(r"(?:名称|资源标题)[：:]\s*(.+?)(?=\n|$)", caption)
        if name_match:
            parsed_data['name'] = name_match.group(1).strip()
        desc_match = re.search(r"描述[：:]\s*(.+?)(?=\n(?:链接|夸克|百度|UC|迅雷|📁|🏷)|$)", caption, re.DOTALL)
        if desc_match:
            parsed_data['description'] = desc_match.group(1).strip()
        link_matches = re.findall(
            r"(?:(?:夸克|百度|UC|迅雷)[：:]\s*)?(https?://(?:pan\.quark\.cn/s/[^\s\n]+|pan\.baidu\.com/s/[^\s\n]+(?:\?pwd=[^\s\n]+)?|drive\.uc\.cn/[^\s\n]+|pan\.xunlei\.com/s/[^\s\n]+(?:\?pwd=[^\s\n]+)?))",
            caption)
        for link in link_matches:
            if link not in parsed_data['links']:
                parsed_data['links'].append(link)
        if not parsed_data['links']:
            generic_links = re.findall(
                r"https?://(?:pan\.quark\.cn/s/[^\s\n]+|pan\.baidu\.com/s/[^\s\n]+(?:\?pwd=[^\s\n]+)?|drive\.uc\.cn/[^\s\n]+|pan\.xunlei\.com/s/[^\s\n]+(?:\?pwd=[^\s\n]+)?)",
                caption)
            parsed_data['links'] = list(dict.fromkeys(generic_links))
        size_match = re.search(r"大小[：:]\s*(.+?)(?=\n|$)", caption)
        if size_match:
            parsed_data['size'] = size_match.group(1).strip()
        else:
            size_icon_match = re.search(r"📁\s*大小[：:]\s*(.+?)(?=\n|$)", caption)
            if size_icon_match:
                parsed_data['size'] = size_icon_match.group(1).strip()
        tag_match = re.search(r"标签[：:]\s*(.+?)(?=\n|$)", caption)
        if tag_match:
            parsed_data['tags'] = tag_match.group(1).strip()
        else:
            tag_icon_match = re.search(r"🏷\s*标签[：:]\s*(.+?)(?=\n|$)", caption)
            if tag_icon_match:
                parsed_data['tags'] = tag_icon_match.group(1).strip()
        return parsed_data

    def create_post_caption(self, post_data):
        copyright_keywords = ['⚠️ 版权：', '版权反馈/DMCA', '📢 频道 👥群组🔍投稿/搜索', '版权', '版权反馈', 'DMCA', '频道',
                              '群组', '投稿', '搜索']
        name = post_data['name']
        description = post_data['description']
        for keyword in copyright_keywords:
            if keyword in name or keyword in description:
                raise ValueError(f"内容包含禁止关键词: {keyword}")
        links_formatted = self.format_links(
            '\n'.join(post_data['links']) if isinstance(post_data['links'], list) else post_data['links'])
        original_tags = post_data['tags']
        if original_tags:
            tags_with_prefix = f"{original_tags} #鹏摇星海"
        else:
            tags_with_prefix = "#鹏摇星海"
        fixed_caption = (
            f"名称：{post_data['name']}\n\n"
            f"描述：{post_data['description']}\n\n"
            f"{links_formatted}\n\n"
            f"📁 大小：{post_data['size']}\n"
            f"🏷 标签：{tags_with_prefix}"
        )
        return self.remove_duplicate_links(fixed_caption)


# 初始化投稿管理器
post_manager = PostManager()


# 所有处理器函数（保持不变，确保 context 是 ContextTypes.DEFAULT_TYPE）
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    template_message = (
        "欢迎使用投稿机器人！\n\n"
        "请选择投稿方式："
    )
    keyboard = [
        [InlineKeyboardButton("📝 快速投稿", callback_data="quick_post")],
        [InlineKeyboardButton("📋 分步投稿", callback_data="step_post")],
        [InlineKeyboardButton("ℹ️ 投稿说明", callback_data="post_info")],
        [InlineKeyboardButton("📂 我的投稿", callback_data="my_posts")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(template_message, reply_markup=reply_markup)
    else:
        await update.message.reply_text(template_message, reply_markup=reply_markup)


async def quick_post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    template_message = (
        "请按照以下格式投稿：\n\n"
        "图片\n\n"
        "名称：资源名称\n"
        "描述：资源描述\n"
        "链接：网盘链接1\n"
        "链接：网盘链接2\n"
        "...\n\n"
        "📁 大小：资源大小\n"
        "🏷 标签：标签1 标签2 ...\n\n"
        "请发送带有图片和说明的投稿内容。"
    )
    keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(template_message, reply_markup=reply_markup)


async def step_post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.callback_query.from_user.id
    user_states[user_id] = {
        'step': 'name',
        'data': post_manager.post_template.copy()
    }
    message = "开始分步投稿流程：\n\n请输入资源名称"
    keyboard = [[InlineKeyboardButton("❌ 取消投稿", callback_data="cancel_step_post")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(message, reply_markup=reply_markup)


async def handle_step_post_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in user_states or 'step' not in user_states[user_id]:
        await handle_message(update, context)
        return
    current_step = user_states[user_id]['step']
    user_data = user_states[user_id]['data']
    step_messages = {
        'name': {
            'save_to': 'name',
            'next_step': 'description',
            'prompt': '请输入资源描述'
        },
        'description': {
            'save_to': 'description',
            'next_step': 'links',
            'prompt': '请输入网盘链接（每行一个链接）'
        },
        'links': {
            'save_to': 'links',
            'next_step': 'size',
            'prompt': '请输入资源大小'
        },
        'size': {
            'save_to': 'size',
            'next_step': 'tags',
            'prompt': '请输入标签（用空格分隔）'
        },
        'tags': {
            'save_to': 'tags',
            'next_step': 'complete',
            'prompt': '请发送封面图片'
        }
    }
    if current_step in step_messages:
        user_data[step_messages[current_step]['save_to']] = update.message.text
        next_step = step_messages[current_step]['next_step']
        user_states[user_id]['step'] = next_step
        message = step_messages[current_step]['prompt']
        if current_step != 'tags':
            message = f"已记录{current_step}。\n\n{message}"
        keyboard = [[InlineKeyboardButton("❌ 取消投稿", callback_data="cancel_step_post")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup)
    elif current_step == 'complete':
        if not update.message.photo:
            await update.message.reply_text("请发送一张图片作为封面！")
            return
        image = update.message.photo[-1].file_id
        user_data['links'] = user_data['links'].split('\n') if isinstance(user_data['links'], str) else user_data[
            'links']
        try:
            caption = post_manager.create_post_caption(user_data)
        except ValueError as e:
            await update.message.reply_text(f"投稿失败：{str(e)}")
            del user_states[user_id]
            return
        if user_id not in user_posts:
            user_posts[user_id] = []
        user_posts[user_id].append({'image': image, 'caption': caption})
        del user_states[user_id]
        await show_post_preview(update, context, user_id)


async def post_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info_message = (
        "投稿格式说明：\n\n"
        "1. 发送一张图片作为封面\n"
        "2. 在图片说明中按格式填写信息：\n"
        "   - 名称：资源名称\n"
        "   - 描述：资源简介\n"
        "   - 链接：每行一个网盘链接（支持夸克、百度、UC、迅雷等）\n"
        "   - 大小：资源大小\n"
        "   - 标签：相关标签（用空格分隔）\n\n"
        "示例：\n"
        "名称：我在顶峰等你(2025)\n"
        "描述：上一世，顾雪茭曾因恋爱脑而高考失利...\n"
        "链接：https://pan.quark.cn/s/635e08a47100\n"
        "链接：https://pan.baidu.com/s/1YFLphV9s8sKIFSchRq0UAA?pwd=pyxh\n"
        "📁 大小：NG\n"
        "🏷 标签：#国剧 #剧情 #爱情 #奇幻"
    )
    keyboard = [[InlineKeyboardButton("◀️ 返回", callback_data="back_to_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(info_message, reply_markup=reply_markup)


async def show_my_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_posts or not user_posts[user_id]:
        message = "您还没有投稿记录。"
        keyboard = [
            [InlineKeyboardButton("📝 开始投稿", callback_data="quick_post")],
            [InlineKeyboardButton("◀️ 返回", callback_data="back_to_main")]
        ]
    else:
        posts_summary = "\n\n".join(
            [f"#{i + 1} 投稿内容：\n{post['caption'][:100]}..." if len(post['caption']) > 100
             else f"#{i + 1} 投稿内容：\n{post['caption']}"
             for i, post in enumerate(user_posts[user_id])]
        )
        message = f"您的投稿记录：\n\n{posts_summary}"
        keyboard = [
            [InlineKeyboardButton("➕ 继续投稿", callback_data="quick_post")],
            [InlineKeyboardButton("🗑 清空投稿", callback_data="clear_posts")],
            [InlineKeyboardButton("◀️ 返回", callback_data="back_to_main")]
        ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(message, reply_markup=reply_markup)


async def show_post_preview(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
    last_post = user_posts[user_id][-1]
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=last_post['image'],
        caption=f"投稿预览：\n{last_post['caption']}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("确认发布", callback_data="confirm_post")],
            [InlineKeyboardButton("重新编辑", callback_data="edit_post")]
        ])
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in user_states and 'step' in user_states[user_id]:
        await handle_step_post_message(update, context)
        return

    if not update.message.photo or not update.message.caption:
        error_message = "投稿格式不正确，请按照模板重新投稿。\n\n"
        error_message += (
            "请按照以下格式投稿：\n\n"
            "图片\n\n"
            "名称：\n\n描述：\n\n链接：\n链接：\n...\n\n"
            "📁 大小：\n🏷 标签："
        )
        keyboard = [
            [InlineKeyboardButton("ℹ️ 查看详细说明", callback_data="post_info")],
            [InlineKeyboardButton("◀️ 返回主菜单", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(error_message, reply_markup=reply_markup)
        return

    image = update.message.photo[-1].file_id
    caption = update.message.caption
    parsed_data = post_manager.strict_mode_parse(caption)

    if not parsed_data['name'] or not parsed_data['description']:
        if post_manager.detect_ad_content(caption):
            await update.message.reply_text(
                "检测到您的投稿可能包含广告内容，无法发布。\n"
                "请确保投稿内容符合规范，仅包含网盘资源链接。"
            )
            return

        pattern = (
            r"名称：\s*.*\n\n"
            r"描述：\s*.*\n\n"
            r"(链接：\s*https?:\/\/[^\s]+\n)+\n"
            r"📁 大小：\s*.*\n"
            r"🏷 标签：\s*.*"
        )

        if not re.search(pattern, caption, re.DOTALL):
            fixed_caption = auto_fix_message(caption)
            if post_manager.detect_ad_content(fixed_caption):
                await update.message.reply_text(
                    "检测到您的投稿可能包含广告内容，无法发布。\n"
                    "请确保投稿内容符合规范，仅包含网盘资源链接。"
                )
                return

            if not re.search(pattern, fixed_caption, re.DOTALL):
                error_message = "投稿格式不正确，请按照模板重新投稿。\n\n"
                error_message += (
                    "请按照以下格式投稿：\n\n"
                    "图片\n\n"
                    "名称：\n\n描述：\n\n链接：\n链接：\n...\n\n"
                    "📁 大小：\n🏷 标签："
                )
                keyboard = [
                    [InlineKeyboardButton("ℹ️ 查看详细说明", callback_data="post_info")],
                    [InlineKeyboardButton("◀️ 返回主菜单", callback_data="back_to_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(error_message, reply_markup=reply_markup)
                return
            caption = fixed_caption

        if user_id not in user_posts:
            user_posts[user_id] = []
        user_posts[user_id].append({'image': image, 'caption': caption})
    else:
        try:
            standard_caption = post_manager.create_post_caption(parsed_data)
            if user_id not in user_posts:
                user_posts[user_id] = []
            user_posts[user_id].append({'image': image, 'caption': standard_caption})
        except ValueError as e:
            await update.message.reply_text(f"投稿被拒绝：{str(e)}")
            return

    await show_post_preview(update, context, user_id)


def auto_fix_message(caption):
    name_match = re.search(r"名称[：:]\s*(.+?)(?=\n|$)", caption)
    desc_match = re.search(r"(?:描述|简介)[：:]\s*(.+?)(?=\n(?:链接|夸克|百度|UC|迅雷|📁|🏷)|$)", caption, re.DOTALL)

    links = []
    link_patterns = [
        r"链接[：:]\s*(https?://[^\s\n]+)",
        r"(夸克|百度|UC|迅雷)[：:]\s*(https?://[^\s\n]+(?:\?pwd=[^\s\n]+)?)",
        r"(https?://(?:pan\.quark\.cn/s/[^\s\n]+|pan\.baidu\.com/s/[^\s\n]+(?:\?pwd=[^\s\n]+)?|drive\.uc\.cn/[^\s\n]+|pan\.xunlei\.com/s/[^\s\n]+(?:\?pwd=[^\s\n]+)?))"
    ]

    for pattern in link_patterns:
        matches = re.findall(pattern, caption)
        for match in matches:
            if isinstance(match, tuple):
                link = match[1] if len(match) > 1 else match[0]
            else:
                link = match
            if link not in links:
                links.append(link)

    links_formatted = [f"链接：{link}" for link in links] if links else ["链接：https://pan.quark.cn/s/3c07afa156f3"]

    size_match = re.search(r"大小[：:]\s*(.+?)(?=\n|$)", caption)
    tag_match = re.search(r"标签[：:]\s*(.+?)(?=\n|$)", caption)

    name = name_match.group(1).strip() if name_match else "未提供"
    description = desc_match.group(1).strip() if desc_match else "未提供"
    size = size_match.group(1).strip() if size_match else "NG"
    tags = tag_match.group(1).strip() if tag_match else "#网盘资源"

    newline = "\n"
    fixed_caption = (
        f"名称：{name}\n\n"
        f"描述：{description}\n\n"
        f"{newline.join(links_formatted)}\n\n"
        f"📁 大小：{size}\n"
        f"🏷 标签：{tags}"
    )

    return fixed_caption


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "back_to_main":
        await start(update, context)
    elif data == "quick_post":
        await quick_post_start(update, context)
    elif data == "step_post":
        await step_post_start(update, context)
    elif data == "post_info":
        await post_info(update, context)
    elif data == "my_posts":
        await show_my_posts(update, context)
    elif data == "clear_posts":
        await clear_posts(update, context)
    elif data == "edit_post":
        await handle_edit_callback(update, context)
    elif data == "confirm_post":
        await handle_confirm_callback(update, context)
    elif data == "cancel_post":
        await cancel_post(update, context)
    elif data == "cancel_step_post":
        await cancel_step_post(update, context)


async def clear_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.callback_query.from_user.id
    if user_id in user_posts:
        del user_posts[user_id]
    await update.callback_query.edit_message_text("投稿记录已清空。")
    await asyncio.sleep(2)
    await start(update, context)


async def handle_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if user_id in user_posts:
        del user_posts[user_id]
    await query.edit_message_text("请重新发送新的投稿内容，格式与之前相同。")


async def handle_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if user_id not in user_posts:
        await query.answer("找不到您的投稿内容，无法发送到频道。")
        return

    success_count = 0
    fail_count = 0

    for post_data in user_posts[user_id]:
        image = post_data['image']
        caption = post_data['caption']

        if post_manager.detect_ad_content(caption):
            await query.answer("检测到广告内容，无法发布。")
            fail_count += 1
            continue

        processed_caption = post_manager.remove_duplicate_links(caption)
        links = re.findall(r"链接：\s*(https?://[^\s\n]+)", processed_caption)

        if not links:
            await query.answer("未识别到任何有效链接，请检查链接格式。")
            await query.edit_message_text("发布失败：未识别到任何有效链接，请检查链接格式。\n\n"
                                          "链接应以以下格式之一开头：\n"
                                          "- https://pan.quark.cn/\n"
                                          "- https://pan.baidu.com/\n"
                                          "- https://drive.uc.cn/\n"
                                          "- https://pan.xunlei.com/\n\n"
                                          "请编辑或重新投稿。")
            return

        link_types = post_manager.identify_link_types(links)
        if not link_types:
            unrecognized_links = []
            for link in links:
                if link.startswith("链接："):
                    url = link[3:].strip()
                else:
                    url = link.strip()
                unrecognized_links.append(url)

            await query.answer("发现未识别的链接类型。")
            await query.edit_message_text(f"发布失败：发现未识别的链接类型。\n\n"
                                          f"未识别的链接：\n" +
                                          "\n".join(unrecognized_links) +
                                          "\n\n链接应以以下格式之一开头：\n"
                                          "- https://pan.quark.cn/\n"
                                          "- https://pan.baidu.com/\n"
                                          "- https://drive.uc.cn/\n"
                                          "- https://pan.xunlei.com/\n\n"
                                          "请编辑或重新投稿。")
            return

        base_channels = CHANNEL_IDS
        base_message = (
            f"{processed_caption}\n"
            f"\n📢 频道：@yunpanNB\n"
            f"👥 群组：@naclzy\n"
            f"🔗 获取更多资源：https://docs.qq.com/aio/DYmZYVGpFVGxOS3NE\n"
            f"🎉 来源：https://link3.cc/pyxh"
        )

        for channel_id in base_channels:
            try:
                await context.bot.send_photo(chat_id=channel_id, photo=image, caption=base_message)
                success_count += 1
            except RetryAfter as e:
                retry_after = e.retry_after
                await asyncio.sleep(retry_after)
                try:
                    await context.bot.send_photo(chat_id=channel_id, photo=image, caption=base_message)
                    success_count += 1
                except:
                    fail_count += 1
                    continue
            except TimedOut:
                await asyncio.sleep(5)
                try:
                    await context.bot.send_photo(chat_id=channel_id, photo=image, caption=base_message)
                    success_count += 1
                except:
                    fail_count += 1
                    continue
            except Exception as e:
                logger.error(f"Error while sending post to channel {channel_id}: {e}")
                fail_count += 1

        for link_type in link_types:
            if link_type in SPECIFIC_CHANNELS:
                specific_caption = post_manager.create_channel_specific_caption(processed_caption, link_type)
                specific_message = (
                    f"{specific_caption}\n"
                    f"📢 频道：@yunpanNB\n"
                    f"👥 群组：@naclzy\n"
                    f"🔗 获取更多资源：https://docs.qq.com/aio/DYmZYVGpFVGxOS3NE\n"
                    f"🔗交流讨论：https://link3.cc/pyxh"
                )
                channel_id = SPECIFIC_CHANNELS[link_type]
                try:
                    await context.bot.send_photo(chat_id=channel_id, photo=image, caption=specific_message)
                    success_count += 1
                except RetryAfter as e:
                    retry_after = e.retry_after
                    await asyncio.sleep(retry_after)
                    try:
                        await context.bot.send_photo(chat_id=channel_id, photo=image, caption=specific_message)
                        success_count += 1
                    except:
                        fail_count += 1
                        continue
                except TimedOut:
                    await asyncio.sleep(5)
                    try:
                        await context.bot.send_photo(chat_id=channel_id, photo=image, caption=specific_message)
                        success_count += 1
                    except:
                        fail_count += 1
                        continue
                except Exception as e:
                    logger.error(f"Error while sending post to channel {channel_id}: {e}")
                    fail_count += 1

    if fail_count == 0:
        await query.answer("内容已成功发布到所有频道！")
        await query.edit_message_text(f"您的投稿已成功发布到所有频道（共{success_count}条）。\n感谢您的支持！")
    else:
        await query.answer("部分内容发布失败")
        await query.edit_message_text(
            f"您的投稿发布完成：\n成功：{success_count}条\n失败：{fail_count}条\n感谢您的支持！")

    if user_id in user_posts:
        del user_posts[user_id]

    await asyncio.sleep(2)
    await start(update, context)


async def cancel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if user_id in user_posts:
        del user_posts[user_id]
    await query.edit_message_text("投稿已取消。")
    await asyncio.sleep(2)
    await start(update, context)


async def cancel_step_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    await query.edit_message_text("分步投稿已取消。")
    await asyncio.sleep(2)
    await start(update, context)


# 本地测试专用：纯长轮询，无 Flask Webhook
def main():
    # 初始化单实例锁
    instance_lock = SingleInstanceLock()

    try:
        # 获取实例锁（防止重复启动）
        if not instance_lock.acquire():
            print("❌ 错误：已有一个机器人实例正在运行！")
            print("请关闭所有 Python 进程后重试（任务管理器 → 结束 python.exe）。")
            sys.exit(1)

        # 初始化机器人应用
        application = Application.builder().token(TOKEN).build()

        # 注册所有处理器
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.PHOTO & filters.CAPTION, handle_message))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_step_post_message))
        application.add_handler(CallbackQueryHandler(button_handler))

        print("✅ 本地测试：启动长轮询（已禁用 Webhook + 跨平台单实例锁定）...")
        print(f"🤖 机器人 Token：{TOKEN[:10]}...（隐藏部分字符）")
        print(f"🔒 已锁定实例，防止重复启动")

        # 启动长轮询（关键参数：drop_pending_updates 丢弃历史更新）
        application.run_polling(
            drop_pending_updates=True,
            timeout=30,
            poll_interval=5  # 轮询间隔 5 秒，减少服务器冲突
        )

    except Exception as e:
        # print(f"❌ 机器人启动失败：{str(e)}")
        logger.error(f"Bot start failed: {str(e)}")
        sys.exit(1)
    finally:
        # 确保程序退出时释放锁
        instance_lock.release()
        print("🔓 实例锁已释放")


if __name__ == "__main__":
    # Windows 系统事件循环兼容性处理（关键）
    if sys.platform.startswith("win"):
        try:
            import asyncio
            from asyncio import WindowsSelectorEventLoopPolicy

            asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())
            print("💻 Windows 系统事件循环已兼容配置")
        except Exception as e:
            print(f"⚠️ Windows 事件循环设置警告：{str(e)}")

    # 启动机器人（带跨平台单实例锁定）
    main()