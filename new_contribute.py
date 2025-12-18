import asyncio
import re
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import RetryAfter, TimedOut

# 配置日志
logging.basicConfig(
    filename="error_log.txt",
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# 机器人配置
# TOKEN = 'telegram_bot_token'
TOKEN = os.getenv("TOKEN")
CHANNEL_IDS = ['@yunpanNB', '@ammmziyuan','@naclyunpan']  # 多个频道ID
SPECIFIC_CHANNELS = {
    'quark': '@yunpanquark',      # 夸克网盘频道
    'baidu': '@yunpanbaidu',      # 百度网盘频道
    'uc': '@pxyunpanuc',          # UC网盘频道
    'xunlei': '@pxyunpanxunlei'   # 迅雷网盘频道
}
# Token 校验
if not TOKEN:
    raise ValueError("❌ 未配置 TOKEN！创建 .env 文件添加 TOKEN=xxx")

# 用户数据存储
user_posts = {}
user_states = {}


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
        """
        格式化链接，确保每行都是"链接：URL"的格式
        """
        links = links_text.split('\n')
        formatted_links = []
        
        for link in links:
            link = link.strip()
            if not link:
                continue
                
            # 如果已经包含"链接："前缀，直接使用
            if link.startswith("链接："):
                formatted_links.append(link)
            # 如果包含网盘类型前缀，提取链接部分
            elif re.match(r"^(夸克|百度|UC|迅雷)：", link):
                actual_link = re.search(r"：\s*(https?://.+)", link)
                if actual_link:
                    formatted_links.append(f"链接：{actual_link.group(1)}")
                else:
                    formatted_links.append(f"链接：{link}")
            # 普通链接添加前缀
            else:
                formatted_links.append(f"链接：{link}")
                
        if not formatted_links:
            formatted_links.append("链接：https://pan.quark.cn/s/3c07afa156f3")
            
        return '\n'.join(formatted_links)

    def remove_duplicate_links(self, caption):
        """
        移除重复链接
        """
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
        """
        识别链接类型
        返回包含所有链接类型的集合
        """
        link_types = set()
        unrecognized_links = []  # 用于存储未识别的链接

        # 确保links是列表格式
        if isinstance(links, str):
            links = [links]

        for link in links:
            # 如果是格式化后的链接，提取URL部分
            if link.startswith("链接："):
                url = link[3:].strip()
            else:
                url = link.strip()

            # 根据URL识别网盘类型
            if 'pan.quark.cn' in url:
                link_types.add('quark')
            elif 'pan.baidu.com' in url:
                link_types.add('baidu')
            elif 'drive.uc.cn' in url:
                link_types.add('uc')
            elif 'pan.xunlei.com' in url:
                link_types.add('xunlei')
            else:
                # 收集未识别的链接
                unrecognized_links.append(url)
                # print(f"未识别的链接类型: {url}")

        # 如果有未识别的链接，记录日志
        if unrecognized_links:
            pass
           # print(f"未识别的链接: {unrecognized_links}")

        # print(f"识别出的链接类型: {link_types}")  # 调试信息
        return link_types

    def get_channels_for_each_link(self, links):
        """
        为每个链接获取应该发送到的频道列表
        """
        link_channel_mapping = []

        # 确保links是列表格式
        if isinstance(links, str):
            links = [links]

        for link in links:
            # 如果是格式化后的链接，提取URL部分
            if link.startswith("链接："):
                url = link[3:].strip()
            else:
                url = link.strip()

            # 确定链接类型和对应的频道
            target_channels = list(CHANNEL_IDS)  # 默认包含汇总和备用频道

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
        """
        根据链接类型获取目标频道列表
        """
        # 获取链接类型
        link_types = self.identify_link_types(links)

        # 如果没有识别出链接类型，返回默认频道
        if not link_types:
            return CHANNEL_IDS

        # 构建目标频道列表
        target_channels = set()

        # 添加汇总频道和备用频道
        target_channels.update(CHANNEL_IDS)

        # 根据链接类型添加对应的专门频道
        for link_type in link_types:
            if link_type in SPECIFIC_CHANNELS:
                target_channels.add(SPECIFIC_CHANNELS[link_type])

        return list(target_channels)

    def create_channel_specific_caption(self, original_caption, link_type):
        """
        为特定频道创建只包含该类型链接的投稿内容
        """
        lines = original_caption.split('\n')
        filtered_lines = []
        keep_link = False

        for line in lines:
            if line.startswith("链接："):
                url = line[3:].strip()
                # 根据链接类型决定是否保留该链接
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
                # 保留非链接行（名称、描述、大小、标签等）
                filtered_lines.append(line)

        return '\n'.join(filtered_lines)
    # 添加检测广告内容的方法
    def detect_ad_content(self, caption):
        """
        检测是否包含广告内容
        """
        ad_keywords = [
            '兼职', '招聘', '游戏代练', '刷单', '刷钻'
        ]
        
        # 检查描述中是否包含广告关键词
        desc_match = re.search(r"描述：\s*(.+?)(?=\n|$)", caption)
        if desc_match:
            description = desc_match.group(1)
            for keyword in ad_keywords:
                if keyword in description:
                    return True
                    
        # 检查链接是否为可疑链接
        link_matches = re.findall(r"链接：\s*(https?://[^\s]+)", caption)
        for link in link_matches:
            # 检查是否为非网盘链接
            if not re.match(r"https?://(pan\.quark\.cn|pan\.baidu\.com|drive\.uc\.cn|pan\.xunlei\.com)/", link):
                # 如果不是网盘链接，检查是否包含可疑关键词
                suspicious_patterns = [
                    r"taobao\.com", r"tmall\.com", r"jd\.com", 
                    r"wechat", r"wx\.qq\.com", r"alipay\.com"
                ]
                for pattern in suspicious_patterns:
                    if re.search(pattern, link):
                        return True
                        
        return False

    # 添加严格模式解析方法
    def strict_mode_parse(self, caption):
        """
        严格模式解析投稿内容，只提取必需字段
        """
        # 初始化数据
        parsed_data = {
            'name': '',
            'description': '',
            'links': [],
            'size': '',
            'tags': ''
        }
        
        # 提取名称（支持"名称"或"资源标题"）
        name_match = re.search(r"(?:名称|资源标题)[：:]\s*(.+?)(?=\n|$)", caption)
        if name_match:
            parsed_data['name'] = name_match.group(1).strip()
        
        # 提取描述
        desc_match = re.search(r"描述[：:]\s*(.+?)(?=\n(?:链接|夸克|百度|UC|迅雷|📁|🏷)|$)", caption, re.DOTALL)
        if desc_match:
            parsed_data['description'] = desc_match.group(1).strip()
        
        # 提取链接
        link_matches = re.findall(r"(?:(?:夸克|百度|UC|迅雷)[：:]\s*)?(https?://(?:pan\.quark\.cn/s/[^\s\n]+|pan\.baidu\.com/s/[^\s\n]+(?:\?pwd=[^\s\n]+)?|drive\.uc\.cn/[^\s\n]+|pan\.xunlei\.com/s/[^\s\n]+(?:\?pwd=[^\s\n]+)?))", caption)
        for link in link_matches:
            if link not in parsed_data['links']:
                parsed_data['links'].append(link)
        
        # 如果没有找到特定格式的链接，尝试查找所有可能的网盘链接
        if not parsed_data['links']:
            generic_links = re.findall(r"https?://(?:pan\.quark\.cn/s/[^\s\n]+|pan\.baidu\.com/s/[^\s\n]+(?:\?pwd=[^\s\n]+)?|drive\.uc\.cn/[^\s\n]+|pan\.xunlei\.com/s/[^\s\n]+(?:\?pwd=[^\s\n]+)?)", caption)
            parsed_data['links'] = list(dict.fromkeys(generic_links))  # 去重但保持顺序
        
        # 提取大小
        size_match = re.search(r"大小[：:]\s*(.+?)(?=\n|$)", caption)
        if size_match:
            parsed_data['size'] = size_match.group(1).strip()
        else:
            # 查找带图标的大小格式
            size_icon_match = re.search(r"📁\s*大小[：:]\s*(.+?)(?=\n|$)", caption)
            if size_icon_match:
                parsed_data['size'] = size_icon_match.group(1).strip()
        
        # 提取标签
        tag_match = re.search(r"标签[：:]\s*(.+?)(?=\n|$)", caption)
        if tag_match:
            parsed_data['tags'] = tag_match.group(1).strip()
        else:
            # 查找带图标的标签格式
            tag_icon_match = re.search(r"🏷\s*标签[：:]\s*(.+?)(?=\n|$)", caption)
            if tag_icon_match:
                parsed_data['tags'] = tag_icon_match.group(1).strip()
        
        return parsed_data

    def create_post_caption(self, post_data, is_submission=False):
        """
        创建标准格式的投稿说明
        is_submission: 是否是最终提交，只有在最终提交时才添加 #鹏摇星海 标签
        """
        # 添加版权相关关键词过滤
        copyright_keywords = ['⚠️ 版权：', '版权反馈/DMCA', '📢 频道 👥群组🔍投稿/搜索', '版权', '版权反馈', 'DMCA', '频道',
                              '群组', '投稿', '搜索']
        name = post_data['name']
        description = post_data['description']

        # 检查名称和描述中是否包含版权相关关键词
        for keyword in copyright_keywords:
            if keyword in name or keyword in description:
                raise ValueError(f"内容包含禁止关键词: {keyword}")

        links_formatted = self.format_links('\n'.join(post_data['links']) if isinstance(post_data['links'], list)
                                            else post_data['links'])

        # 处理标签，确保只在用户提交时添加
        original_tags = post_data.get('tags', '')
        if is_submission:
            tags = f"{original_tags} #鹏摇星海" if original_tags else "#鹏摇星海"
        else:
            tags = original_tags if original_tags else ""

        fixed_caption = (
            f"名称：{post_data['name']}\n\n"
            f"描述：{post_data['description']}\n\n"
            f"{links_formatted}\n\n"
            f"📁 大小：{post_data['size']}\n"
            f"🏷 标签：{tags}"
        )

        return self.remove_duplicate_links(fixed_caption)


# 初始化投稿管理器
post_manager = PostManager()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    启动命令处理函数
    """
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
    """
    开始快速投稿流程
    """
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

    keyboard = [
        [InlineKeyboardButton("◀️ 返回", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(template_message, reply_markup=reply_markup)


async def step_post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    开始分步投稿流程
    """
    user_id = update.callback_query.from_user.id
    user_states[user_id] = {
        'step': 'name',
        'data': post_manager.post_template.copy()
    }

    message = "开始分步投稿流程：\n\n请输入资源名称"

    keyboard = [
        [InlineKeyboardButton("❌ 取消投稿", callback_data="cancel_step_post")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(message, reply_markup=reply_markup)


async def handle_step_post_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理分步投稿的消息
    """
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
        # 保存当前步骤的数据
        user_data[step_messages[current_step]['save_to']] = update.message.text
        next_step = step_messages[current_step]['next_step']
        
        # 更新步骤状态
        user_states[user_id]['step'] = next_step
        
        # 构造回复消息
        message = step_messages[current_step]['prompt']
        if current_step != 'tags':  # tags步骤需要图片而不是文本
            message = f"已记录{current_step}。\n\n{message}"
            
        keyboard = [[InlineKeyboardButton("❌ 取消投稿", callback_data="cancel_step_post")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, reply_markup=reply_markup)
        
    elif current_step == 'complete':
        if not update.message.photo:
            await update.message.reply_text("请发送一张图片作为封面！")
            return
            
        # 完成分步投稿
        image = update.message.photo[-1].file_id
        user_data['links'] = user_data['links'].split('\n') if isinstance(user_data['links'], str) else user_data['links']
        
        # 创建投稿内容
        caption = post_manager.create_post_caption(user_data)
        
        # 保存投稿
        if user_id not in user_posts:
            user_posts[user_id] = []
        user_posts[user_id].append({'image': image, 'caption': caption})
        
        # 清除状态
        del user_states[user_id]
        
        # 显示预览
        await show_post_preview(update, context, user_id)


async def post_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    投稿说明
    """
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

    keyboard = [
        [InlineKeyboardButton("◀️ 返回", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(info_message, reply_markup=reply_markup)


async def show_my_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    显示用户投稿
    """
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
    """
    显示投稿预览
    """
    posts_summary = "\n\n".join(
        [f"#{i + 1} 投稿内容：\n{post['caption']}" for i, post in enumerate(user_posts[user_id])])

    keyboard = [
        [InlineKeyboardButton("✏️ 编辑", callback_data="edit_post")],
        [InlineKeyboardButton("✅ 确认发布", callback_data="confirm_post")],
        [InlineKeyboardButton("❌ 取消", callback_data="cancel_post")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(f"感谢您的投稿！以下是您的所有投稿内容：\n\n{posts_summary}\n\n"
                                    "您可以选择以下操作：", reply_markup=reply_markup)


async def handle_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理编辑回调 - 显示编辑菜单
    """
    query = update.callback_query
    user_id = query.from_user.id

    if user_id not in user_posts or not user_posts[user_id]:
        await query.answer("找不到您的投稿内容")
        return

    # 获取最新的投稿
    latest_post = user_posts[user_id][-1]
    caption = latest_post['caption']

    # 解析投稿内容
    parsed_data = post_manager.strict_mode_parse(caption)

    # 创建编辑菜单
    keyboard = [
        [InlineKeyboardButton(f"✏️ 编辑名称: {parsed_data['name'][:20]}{'...' if len(parsed_data['name']) > 20 else ''}", callback_data="edit_name")],
        [InlineKeyboardButton(f"✏️ 编辑描述: {parsed_data['description'][:20]}{'...' if len(parsed_data['description']) > 20 else ''}", callback_data="edit_description")],
        [InlineKeyboardButton(f"✏️ 编辑链接: {len(parsed_data['links'])}个链接", callback_data="edit_links")],
        [InlineKeyboardButton(f"✏️ 编辑大小: {parsed_data['size']}", callback_data="edit_size")],
        [InlineKeyboardButton(f"✏️ 编辑标签: {parsed_data['tags'][:20]}{'...' if len(parsed_data['tags']) > 20 else ''}", callback_data="edit_tags")],
        [InlineKeyboardButton("✅ 完成编辑", callback_data="finish_edit")],
        [InlineKeyboardButton("❌ 取消编辑", callback_data="cancel_edit")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text("请选择要编辑的字段：", reply_markup=reply_markup)


async def handle_edit_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理编辑特定字段的回调
    """
    query = update.callback_query
    user_id = query.from_user.id
    field_to_edit = query.data.replace("edit_", "")

    # 获取最新的投稿
    latest_post = user_posts[user_id][-1]
    caption = latest_post['caption']

    # 解析投稿内容
    parsed_data = post_manager.strict_mode_parse(caption)

    # 存储当前编辑状态
    user_states[user_id] = {
        'step': f'edit_{field_to_edit}',
        'current_post': {
            'image': latest_post['image'],
            'caption': caption,
            'parsed_data': parsed_data
        },
        'editing_field': field_to_edit
    }

    # 提示用户输入新值
    field_names = {
        'name': '名称',
        'description': '描述',
        'links': '链接（每行一个）',
        'size': '大小',
        'tags': '标签'
    }

    # 显示当前值和输入提示
    current_value = parsed_data[field_to_edit]
    if field_to_edit == 'links' and isinstance(current_value, list):
        current_value = '\n'.join(current_value)
    
    message = f"当前{field_names[field_to_edit]}：\n{current_value}\n\n请输入新的{field_names[field_to_edit]}："
    
    # 添加取消按钮
    keyboard = [[InlineKeyboardButton("❌ 取消编辑当前字段", callback_data="cancel_edit_field")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(message, reply_markup=reply_markup)


async def handle_edit_field_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理用户编辑字段的消息
    """
    user_id = update.message.from_user.id

    if user_id not in user_states or user_states[user_id]['step'] not in ['edit_name', 'edit_description', 'edit_links', 'edit_size', 'edit_tags']:
        await handle_message(update, context)
        return

    # 获取编辑状态
    edit_state = user_states[user_id]
    editing_field = edit_state['editing_field']
    new_value = update.message.text.strip()

    # 验证输入
    if not new_value:
        await update.message.reply_text("输入不能为空，请重新输入！")
        return

    if editing_field == 'links':
        # 处理链接格式
        new_value = new_value.split('\n')
        # 过滤空行
        new_value = [link.strip() for link in new_value if link.strip()]

    # 更新解析数据
    edit_state['current_post']['parsed_data'][editing_field] = new_value

    # 更新投稿内容
    try:
        new_caption = post_manager.create_post_caption(edit_state['current_post']['parsed_data'])
        edit_state['current_post']['caption'] = new_caption
        
        # 更新用户投稿
        user_posts[user_id][-1] = edit_state['current_post']
        
        # 显示编辑成功消息和完整的更新内容
        await update.message.reply_text(f"{editing_field}已更新！\n\n更新后的完整内容：\n{new_caption}")
        await show_post_preview(update, context, user_id)
    except ValueError as e:
        await update.message.reply_text(f"更新失败：{str(e)}")


async def handle_finish_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理完成编辑回调
    """
    query = update.callback_query
    user_id = query.from_user.id

    # 清除编辑状态
    if user_id in user_states:
        del user_states[user_id]

    # 显示更新后的投稿预览
    await show_post_preview(update, context, user_id)


async def handle_cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理取消编辑回调
    """
    query = update.callback_query
    user_id = query.from_user.id

    # 清除编辑状态
    if user_id in user_states:
        del user_states[user_id]

    # 显示原始投稿预览
    await show_post_preview(update, context, user_id)


async def handle_cancel_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理取消编辑当前字段回调
    """
    query = update.callback_query
    user_id = query.from_user.id

    # 清除当前字段编辑状态
    if user_id in user_states and user_states[user_id]['step'].startswith('edit_'):
        # 返回到编辑菜单
        await handle_edit_callback(update, context)


# 修改 handle_message 函数以支持编辑模式
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理用户投稿消息
    """
    user_id = update.message.from_user.id
    
    # 检查是否在编辑模式
    if user_id in user_states and user_states[user_id]['step'].startswith('edit_'):
        await handle_edit_field_message(update, context)
        return

    # 检查是否在分步投稿状态
    if user_id in user_states and 'step' in user_states[user_id]:
        await handle_step_post_message(update, context)
        return

    # 检查投稿内容
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

    # 获取图片和文字内容
    image = update.message.photo[-1].file_id
    caption = update.message.caption

    # 使用严格模式解析投稿内容
    parsed_data = post_manager.strict_mode_parse(caption)
    
    # 如果解析出来的必需字段为空，则使用自动修复
    if not parsed_data['name'] or not parsed_data['description']:
        # 检测广告内容
        if post_manager.detect_ad_content(caption):
            # 如果检测到广告内容，通知用户并拒绝发布
            await update.message.reply_text(
                "检测到您的投稿可能包含广告内容，无法发布。\n"
                "请确保投稿内容符合规范，仅包含网盘资源链接。"
            )
            return

        # 验证格式
        pattern = (
            r"名称：\s*.*\n\n"
            r"描述：\s*.*\n\n"
            r"(链接：\s*https?:\\/\\/[^\s]+\n)+\n"
            r"📁 大小：\s*.*\n"
            r"🏷 标签：\s*.*"
        )

        if not re.search(pattern, caption, re.DOTALL):
            # 尝试自动修复
            fixed_caption = auto_fix_message(caption)
            # 修复后再次检测广告内容
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
            
        # 存储投稿内容
        if user_id not in user_posts:
            user_posts[user_id] = []

        user_posts[user_id].append({'image': image, 'caption': caption})
    else:
        # 使用严格模式解析的数据创建标准格式投稿
        try:
            standard_caption = post_manager.create_post_caption(parsed_data)
            
            # 存储投稿内容
            if user_id not in user_posts:
                user_posts[user_id] = []

            user_posts[user_id].append({'image': image, 'caption': standard_caption})
        except ValueError as e:
            await update.message.reply_text(f"投稿被拒绝：{str(e)}")
            return

    # 显示预览
    await show_post_preview(update, context, user_id)


def auto_fix_message(caption):
    """
    自动修复消息格式
    """
    # 提取各部分内容
    name_match = re.search(r"名称[：:]\s*(.+?)(?=\n|$)", caption)
    desc_match = re.search(r"(?:描述|简介)[：:]\s*(.+?)(?=\n(?:链接|夸克|百度|UC|迅雷|📁|🏷)|$)", caption, re.DOTALL)
    
    # 提取链接
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
    
    # 格式化链接
    links_formatted = [f"链接：{link}" for link in links] if links else ["链接：https://pan.quark.cn/s/3c07afa156f3"]
    
    # 提取大小和标签
    size_match = re.search(r"大小[：:]\s*(.+?)(?=\n|$)", caption)
    tag_match = re.search(r"标签[：:]\s*(.+?)(?=\n|$)", caption)
    
    name = name_match.group(1).strip() if name_match else "未提供"
    description = desc_match.group(1).strip() if desc_match else "未提供"
    size = size_match.group(1).strip() if size_match else "NG"
    tags = tag_match.group(1).strip() if tag_match else "#网盘资源"
    
    # 构建标准格式
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
    """
    处理按钮回调
    """
    query = update.callback_query
    await query.answer()

    handlers = {
        "quick_post": quick_post_start,
        "step_post": step_post_start,
        "post_info": post_info,
        "my_posts": show_my_posts,
        "back_to_main": start,
        "clear_posts": clear_posts,
        "edit_post": handle_edit_callback,
        "confirm_post": handle_confirm_callback,
        "cancel_post": cancel_post,
        "cancel_step_post": cancel_step_post,
        "edit_name": handle_edit_field_callback,
        "edit_description": handle_edit_field_callback,
        "edit_links": handle_edit_field_callback,
        "edit_size": handle_edit_field_callback,
        "edit_tags": handle_edit_field_callback,
        "finish_edit": handle_finish_edit,
        "cancel_edit": handle_cancel_edit,
        "cancel_edit_field": handle_cancel_edit_field
    }

    if query.data in handlers:
        await handlers[query.data](update, context)


async def clear_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    清空投稿记录
    """
    user_id = update.callback_query.from_user.id
    if user_id in user_posts:
        del user_posts[user_id]
    await update.callback_query.edit_message_text("投稿记录已清空。")
    await asyncio.sleep(2)
    await start(update, context)


async def handle_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    处理确认发布回调 - 根据网盘类型发布到对应频道
    """
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

        # 发布前再次检测广告内容
        if post_manager.detect_ad_content(caption):
            await query.answer("检测到广告内容，无法发布。")
            fail_count += 1
            continue

        # 处理重复链接
        processed_caption = post_manager.remove_duplicate_links(caption)
        
        # 在最终提交时添加"鹏摇星海"标签
        if "🏷 标签：" in processed_caption:
            # 如果已经有标签行，检查是否包含"#鹏摇星海"，如果没有则添加
            if "#鹏摇星海" not in processed_caption:
                processed_caption = processed_caption.replace("🏷 标签：", "🏷 标签：#鹏摇星海 ")
        else:
            # 如果没有标签行，添加包含"#鹏摇星海"的标签行
            processed_caption += "\n🏷 标签：#鹏摇星海"

        # 提取链接以确定链接类型
        links = re.findall(r"链接：\s*(https?://[^\s\n]+)", processed_caption)

        # 检查是否有链接
        if not links:
            # 告诉用户没有找到有效的链接
            await query.answer("未识别到任何有效链接，请检查链接格式。")
            await query.edit_message_text("发布失败：未识别到任何有效链接，请检查链接格式。\n\n"
                                         "链接应以以下格式之一开头：\n"
                                         "- https://pan.quark.cn/\n"
                                         "- https://pan.baidu.com/\n"
                                         "- https://drive.uc.cn/\n"
                                         "- https://pan.xunlei.com/\n\n"
                                         "请编辑或重新投稿。")
            return

        # 识别所有链接类型
        link_types = post_manager.identify_link_types(links)

        # 检查是否识别出了链接类型
        if not link_types:
            unrecognized_links = []
            for link in links:
                if link.startswith("链接："):
                    url = link[3:].strip()
                else:
                    url = link.strip()
                unrecognized_links.append(url)

            # 告诉用户有哪些未识别的链接
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

        # 总是发送到汇总频道和备用频道（包含所有链接）
        base_channels = CHANNEL_IDS

        # 构建基础消息内容（包含所有链接）
        base_message = (
            f"{processed_caption}\n"
            f"\n📢 频道：@yunpanNB\n"
            f"👥 群组：@naclzy\n"
            f"🔗 获取更多资源：https://docs.qq.com/aio/DYmZYVGpFVGxOS3NE\n"
            f"🎉 来源：https://link3.cc/pyxh"
        )

        # 发送到汇总频道和备用频道
        for channel_id in base_channels:
            try:
                await context.bot.send_photo(chat_id=channel_id, photo=image, caption=base_message)
                success_count += 1
               # print(f"成功发送到基础频道: {channel_id}")  # 调试信息
            except RetryAfter as e:
                retry_after = e.retry_after
                await asyncio.sleep(retry_after)
                try:
                    await context.bot.send_photo(chat_id=channel_id, photo=image, caption=base_message)
                    success_count += 1
                   # print(f"重试后成功发送到基础频道: {channel_id}")  # 调试信息
                except:
                    fail_count += 1
                   # print(f"发送到基础频道失败: {channel_id}")  # 调试信息
                    continue
            except TimedOut:
                await asyncio.sleep(5)
                try:
                    await context.bot.send_photo(chat_id=channel_id, photo=image, caption=base_message)
                    success_count += 1
                   # print(f"超时后成功发送到基础频道: {channel_id}")  # 调试信息
                except:
                    fail_count += 1
                   # print(f"超时发送到基础频道失败: {channel_id}")  # 调试信息
                    continue
            except Exception as e:
                logger.error(f"Error while sending post to channel {channel_id}: {e}")
                fail_count += 1
              #  print(f"发送到基础频道异常: {channel_id}, 错误: {e}")  # 调试信息

        # 为每种链接类型创建特定内容并发送到对应专门频道
        for link_type in link_types:
            if link_type in SPECIFIC_CHANNELS:
                # 创建只包含该类型链接的投稿内容
                specific_caption = post_manager.create_channel_specific_caption(processed_caption, link_type)

                # 构建专门频道消息内容
                specific_message = (
                    f"{specific_caption}\n"
                    f"📢 频道：@@yunpanNB\n"
                    f"👥 群组：@naclzy\n"
                    f"🔗 获取更多资源：https://docs.qq.com/aio/DYmZYVGpFVGxOS3NE\n"
                    f"🔗交流讨论：https://link3.cc/pyxh"
                )

                # 发送到对应的专门频道
                channel_id = SPECIFIC_CHANNELS[link_type]
                try:
                    await context.bot.send_photo(chat_id=channel_id, photo=image, caption=specific_message)
                    success_count += 1
                   # print(f"成功发送到专门频道 {link_type}: {channel_id}")  # 调试信息
                except RetryAfter as e:
                    retry_after = e.retry_after
                    await asyncio.sleep(retry_after)
                    try:
                        await context.bot.send_photo(chat_id=channel_id, photo=image, caption=specific_message)
                        success_count += 1
                     #   print(f"重试后成功发送到专门频道 {link_type}: {channel_id}")  # 调试信息
                    except:
                        fail_count += 1
                      #  print(f"发送到专门频道 {link_type} 失败: {channel_id}")  # 调试信息
                        continue
                except TimedOut:
                    await asyncio.sleep(5)
                    try:
                        await context.bot.send_photo(chat_id=channel_id, photo=image, caption=specific_message)
                        success_count += 1
                      #  print(f"超时后成功发送到专门频道 {link_type}: {channel_id}")  # 调试信息
                    except:
                        fail_count += 1
                      #  print(f"超时发送到专门频道 {link_type} 失败: {channel_id}")  # 调试信息
                        continue
                except Exception as e:
                    logger.error(f"Error while sending post to channel {channel_id}: {e}")
                    fail_count += 1
                  #  print(f"发送到专门频道 {link_type} 异常: {channel_id}, 错误: {e}")  # 调试信息

    # 回复用户
    if fail_count == 0:
        await query.answer("内容已成功发布到所有频道！")
        await query.edit_message_text(f"您的投稿已成功发布到所有频道（共{success_count}条）。\n感谢您的支持！")
    else:
        await query.answer("部分内容发布失败")
        await query.edit_message_text(
            f"您的投稿发布完成：\n成功：{success_count}条\n失败：{fail_count}条\n感谢您的支持！")

    # 清理数据
    if user_id in user_posts:
        del user_posts[user_id]

    await asyncio.sleep(2)
    await start(update, context)




async def cancel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    取消投稿
    """
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id in user_posts:
        del user_posts[user_id]
        
    await query.edit_message_text("投稿已取消。")
    await asyncio.sleep(2)
    await start(update, context)


async def cancel_step_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    取消分步投稿
    """
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id in user_states:
        del user_states[user_id]
        
    await query.edit_message_text("分步投稿已取消。")
    await asyncio.sleep(2)
    await start(update, context)


def main():
    """
    主函数
    """
    try:
        # 使用更明确的初始化方式
        application = Application.builder().token(TOKEN).build()

        # 添加处理器
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        print("机器人启动中...")
        # 开始轮询
        application.run_polling(drop_pending_updates=True)

    except Exception as e:
        logger.error(f"启动机器人时发生错误: {e}")
        print(f"启动机器人时发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    import asyncio
    import sys

    # Windows兼容性处理
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    main()








