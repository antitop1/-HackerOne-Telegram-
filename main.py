import os
import uuid
import base64
import html
import logging
import re
from datetime import datetime, timedelta
from openai import OpenAI
from telegram import Update, InputFile
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
import paramiko
from scp import SCPClient

# Определяем базовую директорию
base_dir = os.path.dirname(os.path.abspath(__file__))
HTML_TEMPLATE = os.path.join(base_dir, "new_template.html")
CSS_PATH = os.path.join(base_dir, "assets", "css", "styles.css")
PARTICLE_PATH = os.path.join(base_dir, "assets", "img", "particle_star.png")

# =================== НАСТРОЙКИ ===================
os.environ["HTTP_PROXY"] = "socks5://127.0.0.1:2080"
os.environ["HTTPS_PROXY"] = "socks5://127.0.0.1:2080"
TOKEN = "7483804002:BAHwlM9iE6WMCM_tF5qrj3UlPMgwY9QFM6A"
OPENAI_API_KEY = "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"
SSH_HOST = "32.27.196.244"
SSH_USER = "u1516738"
SSH_PASS = "13X1EY68L4fL2jy8"
SSH_PORT = 22
REMOTE_PATH = "/var/www/test/data/www/site.com/reports"
BASE_URL = "https://example.com/reports"

# =================== ЛОГГЕР ===================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# =================== СОСТОЯНИЯ ===================
(
    ONLY_DESCRIPTION,
    ONLY_REQUEST_EXAMPLE,
    ONLY_SCREENSHOT,
    ONLY_CLIENT
) = range(4)
user_data = {}


# =================== SSH ЗАГРУЗКА ===================
def upload_via_ssh(local_path: str, report_uuid: str) -> str:
    try:
        # Создаем SSH-клиент
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Подключаемся к серверу
        ssh.connect(
            hostname=SSH_HOST,
            port=SSH_PORT,
            username=SSH_USER,
            password=SSH_PASS
        )
        logger.info("✅ Успешное подключение по SSH")

        # Создаем SCP-клиент для передачи файлов
        with SCPClient(ssh.get_transport()) as scp:
            # Путь к папке отчета на сервере
            remote_folder = f"{REMOTE_PATH}/{report_uuid}"

            # Создаем папку для отчета
            sftp = ssh.open_sftp()
            try:
                sftp.stat(remote_folder)
            except FileNotFoundError:
                sftp.mkdir(remote_folder)

            # Загружаем файл как index.html внутри папки
            remote_filepath = f"{remote_folder}/index.html"
            scp.put(local_path, remote_filepath)
            logger.info(f"📤 Отчет загружен: {remote_filepath}")

        # Закрываем соединение
        ssh.close()
        return f"{BASE_URL}/{report_uuid}/"

    except Exception as e:
        logger.error(f"❌ Ошибка SSH: {str(e)}")
        return None

# =================== ПАРСЕР GPT ОТВЕТА ===================
def parse_gpt_response(text: str) -> dict:
    result = {
        'name': '',
        'criticality': '',
        'vulnerability_context': '',
        'risks': [],
        'exploitation_mechanism': [],
        'exploitation_conditions': [],
        'recommendations': [],
        'request_example': '',
        'repro_steps': [],
        'tech_details': '',
        'detailed_description': '',
    }
    current_key = None
    list_keys = {'risks', 'recommendations', 'repro_steps',
                 'exploitation_mechanism', 'exploitation_conditions'}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ':' in line and not line.startswith('-'):
            key, value = line.split(':', 1)
            key = key.lower().replace(' ', '_')
            value = value.strip()
            if key in result:
                if key in list_keys:
                    current_key = key
                    if value:
                        result[key].append(value)
                else:
                    result[key] = value
                    current_key = key
            else:
                current_key = None
        elif line.startswith('-') and current_key in list_keys:
            result[current_key].append(line[1:].strip())
        else:
            if current_key and current_key not in list_keys:
                result[current_key] += ' ' + line

    for key in list_keys:
        if key in result and isinstance(result[key], str):
            result[key] = [result[key]]

    for key in result:
        if not result[key]:
            result[key] = ["Информация отсутствует"]

    return result


# =================== GPT ===================
import httpx
from openai import OpenAI

async def generate_fields_with_gpt(description: str, request_example: str) -> dict:
    logger.debug("🧠 Отправка запроса GPT...")
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = (
        f"На основе следующего короткого описания уязвимости:\n\n"
        f"{description}\n\n"
        "Сгенерируй подробный отчет об уязвимости в формате:\n"
        "name: <Название уязвимости>\n"
        "criticality: <Критичность (Критическая/Высокая/Средняя/Низкая)>\n"
        "vulnerability_context: <Контекст уязвимости (1-2 предложения)>\n"
        "risks:\n"
        "- пункт 1\n"
        "- пункт 2\n"
        "exploitation_mechanism:\n"
        "- пункт 1\n"
        "- пункт 2\n"
        "exploitation_conditions:\n"
        "- пункт 1\n"
        "- пункт 2\n"
        "recommendations:\n"
        "- рекомендация 1\n"
        "- рекомендация 2\n"
        "repro_steps:\n"
        "- 1. шаг 1\n"
        "- 2. шаг 2\n"
        "tech_details: <Технические детали>\n"
        "detailed_description: <Подробное описание (2-3 абзаца)>\n\n"
        "Отвечай строго в этом формате, без JSON, без лишних пояснений."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        content = response.choices[0].message.content.strip()
        logger.debug(f"📦 GPT ответ:\n{content}")
        data = parse_gpt_response(content)
        data['request_example'] = request_example

        formatted_recs = []
        for rec in data['recommendations']:
            if ': ' in rec:
                title, desc = rec.split(': ', 1)
                formatted_recs.append(f'<div class="report__h1">{title}</div><div class="text">{desc}</div>')
            else:
                formatted_recs.append(f'<div class="report__h1">{rec}</div>')
        data['formatted_recommendations'] = formatted_recs

    except Exception as e:
        logger.error(f"❌ Ошибка при работе с GPT: {e}")
        return {
            'name': 'Ошибка',
            'criticality': 'Ошибка',
            'vulnerability_context': 'Не удалось сгенерировать контекст',
            'risks': ['Не удалось распарсить ответ GPT'],
            'exploitation_mechanism': ['Не удалось сгенерировать механизм'],
            'exploitation_conditions': ['Не удалось сгенерировать условия'],
            'recommendations': [],
            'request_example': request_example,
            'repro_steps': [],
            'tech_details': '',
            'detailed_description': 'Ошибка при генерации подробного описания.',
            'formatted_recommendations': []
        }

    return data



# =================== FTP ЗАГРУЗКА ===================
def upload_to_ftp(local_path: str, remote_filename: str) -> str:
    try:
        with ftplib.FTP(FTP_HOST) as ftp:
            ftp.login(user=FTP_USER, passwd=FTP_PASS)

            # Пытаемся перейти в целевую директорию
            try:
                ftp.cwd(FTP_PATH)
            except:
                # Создаем только конечную директорию, а не всю структуру
                try:
                    ftp.mkd(FTP_PATH)
                    ftp.cwd(FTP_PATH)
                except Exception as e:
                    logger.error(f"❌ Ошибка создания директории: {e}")
                    return None

            # Загружаем файл
            with open(local_path, 'rb') as f:
                ftp.storbinary(f'STOR {remote_filename}', f)

            logger.debug(f"✅ Файл успешно загружен: {remote_filename}")
            return f"{BASE_URL}/{remote_filename}"

    except Exception as e:
        logger.error(f"❌ Ошибка FTP: {e}")
        return None

# =================== ХЕНДЛЕРЫ ===================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("📄 Сначала отправьте короткое описание уязвимости:")
    return ONLY_DESCRIPTION

async def receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    user_data[chat_id] = {'description': update.message.text}
    await update.message.reply_text("✉️ Теперь отправьте пример запроса (request_example):")
    return ONLY_REQUEST_EXAMPLE

async def receive_request_example(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    user_data[chat_id]['request_example'] = update.message.text
    await update.message.reply_text("🏢 Теперь укажите название клиента (компании):")
    return ONLY_CLIENT

async def receive_client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    user_data[chat_id]['client'] = update.message.text
    await update.message.reply_text("📸 Теперь прикрепите скриншот уязвимости.")
    return ONLY_SCREENSHOT


async def receive_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.message.chat_id
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    screenshot_filename = f"{uuid.uuid4()}.jpg"
    await file.download_to_drive(screenshot_filename)
    user_data[chat_id]['screenshot'] = screenshot_filename

    await update.message.chat.send_action(action=ChatAction.TYPING)

    gpt_fields = await generate_fields_with_gpt(
        user_data[chat_id]['description'],
        user_data[chat_id]['request_example']
    )
    user_data[chat_id].update(gpt_fields)

    report_path = await generate_report(chat_id)

    # Используем UUID для имени папки
    report_uuid = str(uuid.uuid4())

    # Загружаем отчет через SSH
    report_url = upload_via_ssh(report_path, report_uuid)

    if report_url:
        # Отправляем ссылку на отчет
        sales_summary = generate_sales_summary(user_data[chat_id])
        message = (
            f"📊 Отчет об уязвимости:\n"
            f"{report_url}\n\n"
            f"📣 Информация для отдела продаж:\n"
            f"----------------------------\n"
            f"{sales_summary}"
        )
        await update.message.reply_text(message)

    else:
        # Если не удалось загрузить, отправляем файлом
        with open(report_path, 'rb') as f:
            await update.message.reply_document(
                document=InputFile(f, filename=os.path.basename(report_path)),
                caption="✅ Ваш отчёт готов! (не удалось загрузить на сервер)"
            )

    # Удаляем временные файлы
    os.remove(screenshot_filename)
    os.remove(report_path)

    return ConversationHandler.END
# =================== ОТЧЁТ ===================
async def generate_report(chat_id: int) -> str:
    data = user_data[chat_id]

    # Загружаем шаблон
    with open(HTML_TEMPLATE, 'r', encoding='utf-8') as f:
        html_content = f.read()

    # Загружаем и встраиваем CSS
    if os.path.exists(CSS_PATH):
        with open(CSS_PATH, "r", encoding="utf-8") as css_file:
            css_content = css_file.read()
        html_content = html_content.replace(
            '<link href="assets/css/styles.css" rel="stylesheet" />',
            f'<style>{css_content}</style>'
        )

    # Встраиваем particle_star.png
    if os.path.exists(PARTICLE_PATH):
        with open(PARTICLE_PATH, "rb") as particle_file:
            particle_base64 = base64.b64encode(particle_file.read()).decode("utf-8")
        html_content = html_content.replace(
            'src="assets/img/particle_star.png"',
            f'src="data:image/png;base64,{particle_base64}"'
        )

    # Удаляем JavaScript
    html_content = html_content.replace('<script src="assets/js/main.js"></script>', '')

    # Убираем кнопки копирования
    html_content = html_content.replace(
        '<button>\n<svg><use xlink:href="assets/img/icon.svg#copy"></use></svg>\n<span>Копировать</span>\n</button>',
        ''
    )

    # Генерация даты анализа (сегодня - 7 дней)
    today = datetime.today()
    analysis_date = today - timedelta(days=7)
    months_ru = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля",
        5: "мая", 6: "июня", 7: "июля", 8: "августа",
        9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
    }
    current_date = f"{analysis_date.day} {months_ru[analysis_date.month]} {analysis_date.year}"

    # Определение цвета для критичности
    criticality_colors = {
        "Критическая": "red",
        "Высокая": "orange",
        "Средняя": "yellow",
        "Низкая": "green"
    }
    criticality_class = criticality_colors.get(data['criticality'], "red")

    # Вставка скриншота
    screenshot_base64 = ""
    if data.get('screenshot'):
        with open(data['screenshot'], 'rb') as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode('utf-8')
            screenshot_base64 = f'<img src="data:image/jpeg;base64,{encoded_image}" style="max-width:100%;">'

    # Генерация HTML-элементов
    risks_html = "".join([
        f'<li>→ {html.escape(risk)}</li>'
        for risk in data['risks']
    ])

    exploitation_mechanism_html = "".join([
        f'<li>→ {html.escape(item)}</li>'
        for item in data['exploitation_mechanism']
    ])

    exploitation_conditions_html = "".join([
        f'<li>→ {html.escape(item)}</li>'
        for item in data['exploitation_conditions']
    ])

    repro_steps_html = "".join([
        f'<li>{html.escape(step)}</li>'
        for step in data['repro_steps']
    ])

    recommendations_html = "".join(data.get('formatted_recommendations', []))

    # Основные замены в шаблоне
    replacements = {
        '{{CRITICALITY}}': data['criticality'],
        '{{CRITICALITY_CLASS}}': criticality_class,
        '{{VULN_NAME}}': html.escape(data['name']),
        '{{CURRENT_DATE}}': current_date,
        '{{CLIENT}}': html.escape(data.get('client', 'ООО «ТехноСистемс»')),
        '{{DESCRIPTION}}': html.escape(data['detailed_description']),
        '{{SCREENSHOT_SECTION}}': screenshot_base64,
        '{{VULNERABILITY_CONTEXT}}': html.escape(data.get('vulnerability_context', '')),
        '{{RISKS}}': risks_html,
        '{{EXPLOITATION_MECHANISM}}': exploitation_mechanism_html,
        '{{EXPLOITATION_CONDITIONS}}': exploitation_conditions_html,
        '{{REPRO_STEPS}}': repro_steps_html,
        '{{TECH_DETAILS}}': html.escape(data['tech_details']),
        '{{RECOMMENDATIONS}}': recommendations_html,
        '{{REQUEST_EXAMPLE}}': html.escape(data['request_example']).replace('\n', '<br>')
    }

    for placeholder, value in replacements.items():
        html_content = html_content.replace(placeholder, value)

    # Генерация безопасного имени файла
    def sanitize_filename(name):
        # Удаляем запрещенные символы в Windows
        invalid_chars = r'<>:"/\|?*'
        for char in invalid_chars:
            name = name.replace(char, '')
        # Убираем лишние пробелы и сокращаем длину
        name = name.strip().replace('  ', ' ')
        return name[:100]  # Ограничиваем длину имени

    client_name = sanitize_filename(data.get('client', 'Клиент'))
    vuln_name = sanitize_filename(data['name'])
    filename = f"report_{uuid.uuid4()}.html"

    # Сохраняем HTML
    with open(filename, 'w', encoding='utf-8', errors='ignore') as f:
        f.write(html_content)

    return filename


def generate_sales_summary(data: dict) -> str:
    """
    Генерирует обезличенное описание уязвимости для отдела продаж.
    Содержит общую информацию без технических деталей.
    """
    criticality_map = {
        "Критическая": "🔴 Критическая",
        "Высокая": "🟠 Высокая",
        "Средняя": "🟡 Средняя",
        "Низкая": "🟢 Низкая"
    }
    if len(data['vulnerability_context']) < 50:
        data['vulnerability_context'] = (
            "Обнаружена брешь в системе безопасности, позволяющая "
            "злоумышленникам получить несанкционированный доступ "
            "или выполнить вредоносные операции."
        )
    criticality = criticality_map.get(data['criticality'], data['criticality'])

    summary = (
        f"🚨 Обнаружена уязвимость: {data['name']}\n"
        f"⚡️ Критичность: {criticality}\n\n"
        f"📌 Краткое описание:\n"
        f"{data['vulnerability_context']}\n\n"
        f"💼 Для отдела продаж:\n"
        "• Уязвимость позволяет злоумышленникам воздействовать на систему\n"
        "• Может привести к компрометации данных или функционала\n"
        "• Требует оперативного устранения для минимизации рисков\n\n"
        "ℹ️ Детали устранения предоставляются после оформления заказа"
    )

    return summary

# =================== CANCEL ===================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Создание отчёта отменено.")
    return ConversationHandler.END

import asyncio
# =================== MAIN ===================
def main() -> None:
    # Настройки прокси через переменные окружения
    import os
    os.environ["HTTP_PROXY"] = "socks5://127.0.0.1:2080"
    os.environ["HTTPS_PROXY"] = "socks5://127.0.0.1:2080"

    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            ONLY_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_description)],
            ONLY_REQUEST_EXAMPLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_request_example)],
            ONLY_CLIENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_client)],
            ONLY_SCREENSHOT: [MessageHandler(filters.PHOTO, receive_screenshot)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(conv_handler)

    # Настройка логов
    logging.getLogger("httpcore").setLevel(logging.DEBUG)
    logging.getLogger("httpx").setLevel(logging.DEBUG)

    # 🔧 Совместимость с Python 3.8–3.14+
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Запускаем приложение через loop
    loop.run_until_complete(application.run_polling())
    logger.info("Бот запущен...")


if __name__ == "__main__":
    main()