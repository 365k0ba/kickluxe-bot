"""
KickLuxe Bot — для Railway (без прокси, конфиг из env)
"""

import os
import requests
import json
import datetime
import threading
import time
import schedule

# Конфиг из переменных окружения Railway
YANDEX_TOKEN       = os.environ["YANDEX_TOKEN"]
METRIKA_COUNTER_ID = os.environ["METRIKA_COUNTER_ID"]
CLAUDE_API_KEY     = os.environ["CLAUDE_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
CAMPAIGN_ID        = int(os.environ.get("CAMPAIGN_ID", "711370953"))
REPORT_HOUR        = int(os.environ.get("REPORT_HOUR", "9"))
VK_TOKEN           = os.environ.get("VK_TOKEN", "")
VK_GROUP_ID        = os.environ.get("VK_GROUP_ID", "232644257")
VK_POST_HOUR       = int(os.environ.get("VK_POST_HOUR", "12"))

DIRECT_HEADERS = {
    "Authorization": f"Bearer {YANDEX_TOKEN}",
    "Accept-Language": "ru",
    "Content-Type": "application/json; charset=utf-8"
}


# ─── TELEGRAM ───

def tg(method, payload=None):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}",
        json=payload or {}
    )
    return r.json()


def send(text):
    r = tg("sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text[:4096]
    })
    return r


def get_updates(offset=0):
    r = tg("getUpdates", {"offset": offset, "timeout": 30})
    return r.get("result", [])


# ─── МЕТРИКА ───

def get_metrika(date=None):
    day = date or datetime.date.today().isoformat()
    r = requests.get(
        "https://api-metrika.yandex.net/stat/v1/data",
        params={
            "id": METRIKA_COUNTER_ID,
            "metrics": "ym:s:visits,ym:s:bounceRate,ym:s:pageDepth,ym:s:avgVisitDurationSeconds",
            "date1": day, "date2": day,
        },
        headers={"Authorization": f"OAuth {YANDEX_TOKEN}"}
    )
    t = r.json().get("totals", [0, 0, 0, 0])
    return {
        "visits":   int(t[0]),
        "bounce":   round(t[1], 1),
        "depth":    round(t[2], 1),
        "duration": int(t[3])
    }


# ─── ДИРЕКТ ───

def direct(resource, body):
    r = requests.post(
        f"https://api.direct.yandex.com/json/v5/{resource}",
        headers=DIRECT_HEADERS,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8")
    )
    return r.json()


def get_adgroups():
    res = direct("adgroups", {"method": "get", "params": {
        "SelectionCriteria": {"CampaignIds": [CAMPAIGN_ID]},
        "FieldNames": ["Id", "Name"]
    }})
    return res.get("result", {}).get("AdGroups", [])


def get_keywords():
    groups = get_adgroups()
    if not groups:
        return []
    gids = [g["Id"] for g in groups]
    res = direct("keywords", {"method": "get", "params": {
        "SelectionCriteria": {"AdGroupIds": gids},
        "FieldNames": ["Id", "Keyword", "AdGroupId"]
    }})
    return res.get("result", {}).get("Keywords", [])


def get_campaign_status():
    res = direct("campaigns", {"method": "get", "params": {
        "SelectionCriteria": {"Ids": [CAMPAIGN_ID]},
        "FieldNames": ["Id", "Name", "Status", "State"]
    }})
    camps = res.get("result", {}).get("Campaigns", [])
    return camps[0] if camps else None


def apply_actions(actions):
    log = []
    groups = get_adgroups()
    keywords = get_keywords()

    add_kw = actions.get("add_keywords", [])
    if add_kw and groups:
        gid = groups[0]["Id"]
        existing = {k["Keyword"].lower() for k in keywords}
        new_kw = [kw for kw in add_kw if kw.lower() not in existing]
        if new_kw:
            res = direct("keywords", {"method": "add", "params": {
                "Keywords": [{"Keyword": kw, "AdGroupId": gid} for kw in new_kw]
            }})
            added = sum(1 for r in res.get("result", {}).get("AddResults", []) if "Id" in r)
            log.append(f"✅ Добавлено ключей: {added}")

    remove_kw = actions.get("remove_keywords", [])
    if remove_kw and keywords:
        existing_map = {k["Keyword"].lower(): k["Id"] for k in keywords}
        ids_del = [existing_map[kw.lower()] for kw in remove_kw if kw.lower() in existing_map]
        if ids_del:
            direct("keywords", {"method": "delete", "params": {"Ids": ids_del}})
            log.append(f"🗑 Удалено ключей: {len(ids_del)}")

    minus = actions.get("add_minus_words", [])
    if minus and groups:
        updates = [{"Id": g["Id"], "NegativeKeywords": minus} for g in groups]
        direct("adgroups", {"method": "update", "params": {"AdGroups": updates}})
        log.append(f"🚫 Минус-слова: {len(minus)} шт.")

    return log


# ─── CLAUDE ───

def ask_claude(prompt):
    system = """Ты — ИИ-аналитик магазина KickLuxe (премиум кроссовки: Prada, Armani, BOSS, Brunello Cucinelli, Hide & Jack).

Твой ответ — ДВЕ части, разделённые строкой ---JSON---:

ЧАСТЬ 1: Отчёт (эмодзи, обычный текст):
📊 ИТОГИ ДНЯ
✅ ЧТО РАБОТАЕТ
❌ ПРОБЛЕМЫ
🎯 РЕКОМЕНДАЦИИ (3-5 штук, конкретные)

---JSON---

ЧАСТЬ 2: Только валидный JSON:
{
  "add_keywords": [],
  "remove_keywords": [],
  "add_minus_words": []
}"""

    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1500,
            "system": system,
            "messages": [{"role": "user", "content": prompt}]
        }
    )
    if r.status_code != 200:
        return f"Ошибка Claude {r.status_code}", {}

    text = r.json()["content"][0]["text"]
    if "---JSON---" in text:
        parts = text.split("---JSON---", 1)
        report = parts[0].strip()
        try:
            actions = json.loads(parts[1].strip())
        except Exception:
            actions = {}
    else:
        report = text
        actions = {}
    return report, actions


# ─── ВК АВТОПОСТИНГ ───

def vk_get_products():
    r = requests.get("https://api.vk.com/method/market.get", params={
        "owner_id": f"-{VK_GROUP_ID}",
        "count": 100,
        "extended": 1,
        "access_token": VK_TOKEN,
        "v": "5.199"
    })
    items = r.json().get("response", {}).get("items", [])
    return items


def vk_write_post(text, photo_url=None, attachments=None):
    params = {
        "owner_id": f"-{VK_GROUP_ID}",
        "from_group": 1,
        "message": text,
        "access_token": VK_TOKEN,
        "v": "5.199"
    }
    if attachments:
        params["attachments"] = attachments
    r = requests.post("https://api.vk.com/method/wall.post", params=params)
    return r.json()


def vk_daily_post():
    print(f"[{datetime.datetime.now():%H:%M}] ВК автопостинг...")
    try:
        products = vk_get_products()
        if not products:
            print("ВК: товары не найдены")
            send("⚠️ ВК: товары не найдены в магазине")
            return

        # Выбираем товар по дню недели (чередуем)
        day_idx = datetime.date.today().toordinal() % len(products)
        product = products[day_idx]

        name = product.get("title", "Кроссовки")
        price = product.get("price", {}).get("amount", 0)
        price_str = f"{int(price) // 100:,}".replace(",", " ") + " ₽" if price else ""
        description = product.get("description", "")

        # Берём фото товара
        photos = product.get("photos", [])
        attachment = None
        if photos:
            p = photos[0]
            owner = p.get("owner_id")
            pid = p.get("id")
            attachment = f"photo{owner}_{pid}"

        # Генерируем текст через Claude
        prompt = f"""Напиши продающий пост ВКонтакте для магазина премиум кроссовок KickLuxe.

Товар: {name}
Цена: {price_str}
Описание: {description[:300] if description else 'премиальные кроссовки'}

Требования:
- 3-5 предложений, живой и эмоциональный текст
- Подчеркни эксклюзивность и качество
- В конце призыв написать в личные сообщения сообщества
- Добавь 3-5 релевантных хэштегов
- Без лишних вступлений, сразу текст поста"""

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}]
            }
        )
        if r.status_code == 200:
            post_text = r.json()["content"][0]["text"]
        else:
            post_text = f"✨ {name}\n\nПремиум качество по лучшей цене. Пишите в сообщения!\n\n#кроссовки #премиум #KickLuxe"

        result = vk_write_post(post_text, attachments=attachment)
        post_id = result.get("response", {}).get("post_id")

        if post_id:
            print(f"ВК: пост опубликован #{post_id}")
            send(f"✅ ВК пост опубликован!\nТовар: {name}\nvk.com/wall-{VK_GROUP_ID}_{post_id}")
        else:
            print(f"ВК ошибка: {result}")
            send(f"⚠️ ВК ошибка публикации: {result}")

    except Exception as e:
        print(f"ВК ошибка: {e}")
        send(f"⚠️ ВК ошибка: {e}")


# ─── КОМАНДЫ ───

def cmd_report():
    send("⏳ Формирую отчёт...")
    m = get_metrika()
    kw = get_keywords()
    kw_list = ", ".join(k["Keyword"] for k in kw[:20]) or "нет данных"

    prompt = f"""Отчёт KickLuxe за {datetime.date.today()}

Метрика:
- Визиты: {m['visits']}
- Отказы: {m['bounce']}%
- Глубина: {m['depth']} стр.
- Время: {m['duration']} сек.

Ключевые слова: {kw_list}"""

    report, actions = ask_claude(prompt)
    send(f"📋 Отчёт KickLuxe за {datetime.date.today().strftime('%d.%m.%Y')}\n\n{report}")
    return actions


def cmd_optimize():
    send("🔍 Анализирую и применяю рекомендации...")
    actions = cmd_report()
    if actions and any(actions.values()):
        log = apply_actions(actions)
        send("🔧 Применено:\n" + "\n".join(log) if log else "ℹ️ Нет новых действий.")
    else:
        send("ℹ️ ИИ не нашёл действий для применения.")


def cmd_status():
    camp = get_campaign_status()
    m = get_metrika()
    kw = get_keywords()
    status_map = {
        "ACCEPTED": "✅ Активна",
        "MODERATION": "⏳ На модерации",
        "REJECTED": "❌ Отклонена",
    }
    status = status_map.get(camp.get("Status", ""), "?") if camp else "не найдена"
    send(
        f"📊 Статус KickLuxe\n\n"
        f"Кампания: {status}\n"
        f"Ключевых слов: {len(kw)}\n\n"
        f"Метрика сегодня:\n"
        f"• Визиты: {m['visits']}\n"
        f"• Отказы: {m['bounce']}%\n"
        f"• Время: {m['duration']} сек."
    )


def cmd_vkpost():
    send("📸 Публикую пост в ВК...")
    vk_daily_post()


def cmd_help():
    send(
        "🤖 KickLuxe Bot\n\n"
        "/report — отчёт прямо сейчас\n"
        "/optimize — ИИ анализирует и применяет изменения\n"
        "/status — статус кампании\n"
        "/vkpost — опубликовать пост в ВК сейчас\n"
        "/help — эта подсказка\n\n"
        f"Ежедневный отчёт в {REPORT_HOUR:02d}:00.\n"
        f"Пост в ВК каждый день в {VK_POST_HOUR:02d}:00."
    )


# ─── ПЛАНИРОВЩИК ───

def scheduler_loop():
    schedule.every().day.at(f"{REPORT_HOUR:02d}:00").do(lambda: cmd_report())
    schedule.every().day.at(f"{VK_POST_HOUR:02d}:00").do(vk_daily_post)
    while True:
        schedule.run_pending()
        time.sleep(60)


# ─── MAIN ───

def main():
    print("KickLuxe Bot запущен на Railway")
    print(f"CHAT_ID настроен: {TELEGRAM_CHAT_ID}")

    # Проверяем Telegram
    me = tg("getMe")
    print(f"Bot info: {me}")

    r = send(
        "✅ KickLuxe Bot запущен!\n\n"
        "/report — отчёт сейчас\n"
        "/optimize — применить рекомендации ИИ\n"
        "/status — статус кампании\n"
        "/vkpost — пост в ВК сейчас\n"
        f"\nОтчёт в {REPORT_HOUR:02d}:00. Пост в ВК в {VK_POST_HOUR:02d}:00."
    )
    print(f"Отправка приветствия: {r}")

    threading.Thread(target=scheduler_loop, daemon=True).start()

    offset = 0
    while True:
        try:
            updates = get_updates(offset)
            if updates:
                print(f"Получено обновлений: {len(updates)}")
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))
                print(f"Сообщение от chat_id={chat_id}, текст={text!r}")
                if chat_id != TELEGRAM_CHAT_ID:
                    print(f"Игнорирую: {chat_id} != {TELEGRAM_CHAT_ID}")
                    continue
                print(f"Команда: {text}")
                if text.startswith("/report"):
                    threading.Thread(target=cmd_report).start()
                elif text.startswith("/optimize"):
                    threading.Thread(target=cmd_optimize).start()
                elif text.startswith("/status"):
                    threading.Thread(target=cmd_status).start()
                elif text.startswith("/vkpost"):
                    threading.Thread(target=cmd_vkpost).start()
                elif text.startswith("/help") or text.startswith("/start"):
                    cmd_help()
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()

