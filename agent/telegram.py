"""Daily Telegram message: new fitting vacancies + short status footer."""
import html
import os

import requests

LIMIT = 3800  # Telegram max is 4096


def norm_chat(chat: str) -> str:
    """Numeric ids (incl. -100...) stay as is; a bare channel name gets the required @."""
    chat = chat.strip().strip("\"'")
    if chat and not chat.lstrip("-").isdigit() and not chat.startswith("@"):
        return "@" + chat
    return chat


def _item(j):
    bits = [x for x in (j.get("location"), j.get("relocation_text")) if x]
    out = f'<b>{html.escape(j["company"])}</b> — <a href="{html.escape(j["url"], quote=True)}">{html.escape(j["title"])}</a>'
    if bits:
        out += "\n📍 " + html.escape(" · ".join(bits))
    if j.get("reason"):
        out += "\n💬 " + html.escape(j["reason"])
    return out


def build_messages(today, open_fit, new_urls, failed, credits, repo="", max_items=60, unjudged=0, gemini=None):
    from .report import RELOC

    new = [dict(j, relocation_text=RELOC.get(j.get("relocation", ""), "")) for j in open_fit if j["url"] in new_urls]
    head = f"<b>Вакансии {today}</b>\n"
    if new:
        head += f"🆕 Новых подходящих: {len(new)}"
    else:
        head += "Новых подходящих вакансий нет."
    foot = [f"Всего открытых подходящих: {len(open_fit)}"]
    if failed:
        foot.append(f"Не прочитано компаний: {len(failed)} (" + ", ".join(html.escape(n) for n, _ in failed[:12]) + (" …" if len(failed) > 12 else "") + ")")
    if unjudged:
        foot.append(f"⚠️ ИИ не оценил вакансий: {unjudged} (повторю при следующем запуске; проверьте GEMINI_API_KEY)")
    if gemini and gemini.get("req"):
        n = lambda v: f"{v:,}".replace(",", " ")
        foot.append(f"Gemini: запросов {gemini['req']}, токенов вход {n(gemini['in'])} / выход {n(gemini['out'])}")
    if gemini and gemini.get("rules"):
        foot.append(f"Решено правилами без ИИ: {gemini['rules']}")
    if credits:
        foot.append(f"scrape.do: потрачено кредитов {credits}")
    if repo:
        foot.append(f'<a href="https://github.com/{repo}/blob/main/reports/latest.md">Полный отчёт</a>')
    shown = new[:max_items]
    if len(new) > max_items:
        foot.insert(0, f"Показано {max_items} из {len(new)}, остальные в отчёте.")
    blocks = [_item(j) for j in shown] + ["\n".join(foot)]
    msgs, cur = [], head
    for b in blocks:
        if len(cur) + len(b) + 2 > LIMIT:
            msgs.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + b
    msgs.append(cur)
    return msgs


def send(messages):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), norm_chat(os.environ.get("TELEGRAM_CHAT_ID", ""))
    if not token or not chat:
        print("Telegram: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID не заданы, сообщение не отправлено.")
        return False
    ok = True
    for m in messages:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=30,
            )
            if r.status_code != 200:
                print(f"Telegram: HTTP {r.status_code} {r.text[:200]}")
                if "chat not found" in r.text:
                    print("Подсказка: TELEGRAM_CHAT_ID неверен, либо вы не написали этому боту /start. Запустите workflow «Telegram check».")
                ok = False
        except requests.RequestException:
            print("Telegram: ошибка соединения")
            ok = False
    return ok


def check():
    """Print which bot this token belongs to and which chats have written to it; send a test message."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), norm_chat(os.environ.get("TELEGRAM_CHAT_ID", ""))
    if not token:
        print("TELEGRAM_BOT_TOKEN не задан.")
        return
    me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=30).json()
    print("Бот:", (me.get("result") or {}).get("username"), "| ответ ok =", me.get("ok"))
    up = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
    chats = {}
    for u in up.get("result", []):
        c = (u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}).get("chat")
        if c:
            chats[c["id"]] = f'{c.get("type")} {c.get("title") or c.get("username") or c.get("first_name", "")}'
    print("Чаты, писавшие боту (id: тип имя):", chats or "нет (напишите боту /start и запустите снова)")
    print("TELEGRAM_CHAT_ID в секрете сейчас:", repr(chat), "(длина", len(chat), ")")
    if chat:
        gc = requests.get(f"https://api.telegram.org/bot{token}/getChat", params={"chat_id": chat}, timeout=30).json()
        c = gc.get("result") or {}
        print("getChat:", "ok" if gc.get("ok") else gc.get("description"), "|", c.get("type"), c.get("title") or c.get("username") or c.get("first_name"))
        m = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": "Проверка связи: бот вакансий подключён."}, timeout=30).json()
        print("sendMessage:", "ok" if m.get("ok") else m.get("description"))
    print("Для канала id выглядит как -100XXXXXXXXXX, бот должен быть администратором с правом публиковать сообщения.")


if __name__ == "__main__":
    check()
