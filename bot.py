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


# ─── ВОРДСТАТ (через Директ API) ───

def wordstat_get_suggestions(keywords):
    """Получаем похожие запросы и частотность через Директ API."""
    body = {
        "method": "get",
        "params": {
            "Keywords": keywords[:10],
            "Language": "RU"
        }
    }
    r = requests.post(
        "https://api.direct.yandex.com/json/v5/keywordsresearch",
        headers=DIRECT_HEADERS,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8")
    )
    return r.json()


def wordstat_weekly():
    """Еженедельный анализ ключевых слов через Вордстат."""
    print(f"[{datetime.datetime.now():%H:%M}] Вордстат анализ...")
    send("🔍 Запускаю еженедельный анализ Вордстат...")

    try:
        # Получаем текущие ключи
        current_kw = get_keywords()
        current_list = [k["Keyword"] for k in current_kw]

        # Базовые seed-запросы для расширения семантики
        seeds = [
            "купить кроссовки Prada",
            "кроссовки Armani мужские",
            "кроссовки BOSS стиль",
            "Brunello Cucinelli кроссовки",
            "Hide Jack кроссовки",
            "премиум кроссовки купить",
            "дизайнерские кроссовки",
            "люкс кроссовки",
        ]

        # Запрашиваем предложения
        result = wordstat_get_suggestions(seeds)
        suggestions = []

        if "result" in result:
            for item in result.get("result", {}).get("KeywordsResearch", []):
                for kw_data in item.get("Keywords", []):
                    kw = kw_data.get("Keyword", "")
                    freq = kw_data.get("Frequency", 0)
                    if freq > 50 and kw.lower() not in [c.lower() for c in current_list]:
                        suggestions.append({"keyword": kw, "freq": freq})

            suggestions.sort(key=lambda x: x["freq"], reverse=True)
        else:
            # Если API не поддерживает — используем Claude для генерации
            suggestions = []

        # Отправляем данные в Claude для анализа
        current_str = "\n".join(f"- {k}" for k in current_list[:30])
        suggest_str = "\n".join(f"- {s['keyword']} (частота: {s['freq']})" for s in suggestions[:20]) if suggestions else "нет данных от API"

        prompt = f"""Ты SEO-аналитик магазина KickLuxe (реплики премиум кроссовок: Prada, Armani, BOSS, Brunello Cucinelli, Hide & Jack).

Текущие ключевые слова в кампании:
{current_str}

Данные Яндекс Вордстат (похожие запросы):
{suggest_str}

Задача: расширь семантическое ядро для кампании.

Ответ в формате (строго):
АНАЛИЗ:
[3-4 предложения что работает и что упускаем]

ДОБАВИТЬ (15-20 новых ключей, которых ещё нет в кампании):
- ключевое слово 1
- ключевое слово 2
...

МИНУС-СЛОВА (10-15 слов для исключения нецелевого трафика):
- слово1
- слово2
..."""

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1500,
                  "messages": [{"role": "user", "content": prompt}]}
        )

        if r.status_code != 200:
            send(f"⚠️ Ошибка Claude: {r.status_code}")
            return

        text = r.json()["content"][0]["text"]

        # Парсим ответ
        new_keywords = []
        minus_words = []
        in_add = False
        in_minus = False

        for line in text.split("\n"):
            line = line.strip()
            if "ДОБАВИТЬ" in line:
                in_add = True
                in_minus = False
            elif "МИНУС" in line:
                in_add = False
                in_minus = True
            elif line.startswith("- ") and in_add:
                new_keywords.append(line[2:].strip())
            elif line.startswith("- ") and in_minus:
                minus_words.append(line[2:].strip())

        # Применяем изменения
        applied = []
        groups = get_adgroups()
        existing_kw = {k["Keyword"].lower() for k in current_kw}

        if new_keywords and groups:
            gid = groups[0]["Id"]
            to_add = [kw for kw in new_keywords if kw.lower() not in existing_kw][:20]
            if to_add:
                res = direct("keywords", {"method": "add", "params": {
                    "Keywords": [{"Keyword": kw, "AdGroupId": gid} for kw in to_add]
                }})
                added = sum(1 for r in res.get("result", {}).get("AddResults", []) if "Id" in r)
                applied.append(f"✅ Добавлено ключей: {added}")

        if minus_words and groups:
            updates = [{"Id": g["Id"], "NegativeKeywords": minus_words} for g in groups]
            direct("adgroups", {"method": "update", "params": {"AdGroups": updates}})
            applied.append(f"🚫 Минус-слова обновлены: {len(minus_words)} шт.")

        # Отправляем отчёт
        report_text = text.split("ДОБАВИТЬ")[0].replace("АНАЛИЗ:", "").strip()
        msg = (
            f"📊 Еженедельный Вордстат-анализ\n\n"
            f"{report_text}\n\n"
            f"{'Применено:' if applied else 'Изменений нет.'}\n"
            + "\n".join(applied)
        )
        send(msg)
        print("Вордстат анализ завершён")

    except Exception as e:
        print(f"Вордстат ошибка: {e}")
        send(f"⚠️ Вордстат ошибка: {e}")


# ─── CLAUDE ───

def ask_claude(prompt):
    system = """Ты — ИИ-аналитик магазина KickLuxe (реплики премиум кроссовок: Prada, Armani, BOSS, Brunello Cucinelli, Hide & Jack).

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

PRODUCTS = [
    {
        "name": "Brunello Cucinelli",
        "desc": "Философия медленной моды. Стильный дизайн, безупречный силуэт — кроссовки как произведение искусства.",
        "photos": [
            "photo-232644257_457239167",
            "photo-232644257_457239169",
            "photo-232644257_457239176",
            "photo-232644257_457239179",
            "photo-232644257_457239180",
        ]
    },
    {
        "name": "Hide & Jack",
        "desc": "Итальянский бренд нового поколения. Яркие цвета, смелый дизайн для тех, кто хочет выделяться.",
        "photos": [
            "photo-232644257_457239168",
            "photo-232644257_457239171",
            "photo-232644257_457239173",
            "photo-232644257_457239184",
            "photo-232644257_457239187",
        ]
    },
    {
        "name": "Hugo BOSS",
        "desc": "Современная мужская классика. Точный крой, уверенный стиль и узнаваемый дизайн.",
        "photos": [
            "photo-232644257_457239170",
            "photo-232644257_457239172",
            "photo-232644257_457239174",
            "photo-232644257_457239175",
        ]
    },
    {
        "name": "Giorgio Armani",
        "desc": "Элегантность без компромиссов. Минимализм и безупречный стиль от легендарного итальянского дома.",
        "photos": [
            "photo-232644257_457239177",
            "photo-232644257_457239183",
            "photo-232644257_457239185",
            "photo-232644257_457239186",
        ]
    },
    {
        "name": "Prada",
        "desc": "Итальянская роскошь в каждой детали. Узнаваемый силуэт, статус и стиль на каждом шагу.",
        "photos": [
            "photo-232644257_457239188",
            "photo-232644257_457239182",
            "photo-232644257_457239181",
            "photo-232644257_457239178",
        ]
    },
]


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


def vk_upload_photo(image_url):
    """Загружает фото по URL на стену ВК и возвращает attachment строку."""
    try:
        # Шаг 1: получаем сервер загрузки
        r = requests.get("https://api.vk.com/method/photos.getWallUploadServer", params={
            "group_id": VK_GROUP_ID,
            "access_token": VK_TOKEN,
            "v": "5.199"
        })
        upload_url = r.json()["response"]["upload_url"]

        # Шаг 2: скачиваем фото и загружаем на сервер ВК
        img_data = requests.get(image_url).content
        upload_r = requests.post(upload_url, files={"photo": ("photo.jpg", img_data, "image/jpeg")})
        uploaded = upload_r.json()

        # Шаг 3: сохраняем фото
        save_r = requests.post("https://api.vk.com/method/photos.saveWallPhoto", params={
            "group_id": VK_GROUP_ID,
            "photo": uploaded["photo"],
            "server": uploaded["server"],
            "hash": uploaded["hash"],
            "access_token": VK_TOKEN,
            "v": "5.199"
        })
        photo = save_r.json()["response"][0]
        return f"photo{photo['owner_id']}_{photo['id']}"
    except Exception as e:
        print(f"Ошибка загрузки фото: {e}")
        return None


def vk_daily_post(random_product=False):
    print(f"[{datetime.datetime.now():%H:%M}] ВК автопостинг...")
    try:
        if random_product:
            import random
            product = random.choice(PRODUCTS)
        else:
            day_idx = datetime.date.today().toordinal() % len(PRODUCTS)
            product = PRODUCTS[day_idx]
        name = product["name"]
        desc = product["desc"]
        photos = product["photos"]

        # Выбираем 1-2 фото для поста (чередуем)
        photo_idx = (datetime.date.today().toordinal() // len(PRODUCTS)) % len(photos)
        selected_photos = [photos[photo_idx], photos[(photo_idx + 1) % len(photos)]]

        # Генерируем текст через Claude
        prompt = f"""Напиши продающий пост ВКонтакте для магазина KickLuxe, который продаёт реплики премиум кроссовок.

Бренд: {name}
Описание: {desc}

Требования:
- 3-5 предложений, живой и эмоциональный текст
- Пиши про стиль, внешний вид, дизайн — НЕ пиши "оригинал", "настоящая кожа", "оригинальное качество"
- Акцент на доступность премиального стиля, внешний вид как у люкса
- Упомяни доставку по России и оплату после примерки
- В конце: "Пишите в сообщения или на сайт kickluxe.ru"
- Добавь 4-5 хэштегов: #KickLuxe #кроссовки #{name.replace(' ', '')} #стиль #премиумлук
- Без вступлений, сразу текст поста"""

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 500,
                  "messages": [{"role": "user", "content": prompt}]}
        )
        post_text = r.json()["content"][0]["text"] if r.status_code == 200 else \
            f"✨ {name}\n\n{desc}\n\nДоставка по России. Оплата после примерки.\nkickluxe.ru\n\n#KickLuxe #премиум #люкс"

        result = vk_write_post(post_text, attachments=",".join(selected_photos))
        post_id = result.get("response", {}).get("post_id")

        if post_id:
            print(f"ВК: пост #{post_id} опубликован")
            send(f"✅ ВК пост опубликован — {name}\nvk.com/wall-{VK_GROUP_ID}_{post_id}")
        else:
            print(f"ВК ошибка: {result}")
            send(f"⚠️ ВК ошибка: {result}")

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


def get_direct_stats():
    """Статистика кампании из Директа за сегодня."""
    today = datetime.date.today().isoformat()
    res = direct("reports", {
        "method": "get",
        "params": {
            "SelectionCriteria": {
                "DateFrom": today,
                "DateTo": today,
                "Filter": [{"Field": "CampaignId", "Operator": "IN", "Values": [str(CAMPAIGN_ID)]}]
            },
            "FieldNames": ["Impressions", "Clicks", "Ctr", "Cost"],
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "NO",
            "IncludeDiscount": "NO"
        }
    })
    try:
        lines = res.strip().split("\n")
        if len(lines) >= 3:
            data = lines[2].split("\t")
            return {
                "impressions": int(data[0]) if data[0] != "--" else 0,
                "clicks": int(data[1]) if data[1] != "--" else 0,
                "ctr": float(data[2]) if data[2] != "--" else 0.0,
                "cost": round(float(data[3]) / 1000000, 2) if data[3] != "--" else 0.0
            }
    except Exception:
        pass
    return {"impressions": 0, "clicks": 0, "ctr": 0.0, "cost": 0.0}


def cmd_status():
    camp = get_campaign_status()
    m = get_metrika()
    kw = get_keywords()
    d = get_direct_stats()

    status_map = {
        "ACCEPTED": "✅ Активна",
        "MODERATION": "⏳ На модерации",
        "REJECTED": "❌ Отклонена",
    }
    status = status_map.get(camp.get("Status", ""), "?") if camp else "не найдена"

    send(
        f"📊 Статус KickLuxe — {datetime.date.today().strftime('%d.%m.%Y')}\n\n"
        f"🎯 Кампания: {status}\n"
        f"🔑 Ключевых слов: {len(kw)}\n\n"
        f"📣 Директ сегодня:\n"
        f"• Показы: {d['impressions']}\n"
        f"• Клики: {d['clicks']}\n"
        f"• CTR: {d['ctr']}%\n"
        f"• Расход: {d['cost']} ₽\n\n"
        f"🌐 Метрика сегодня:\n"
        f"• Визиты: {m['visits']}\n"
        f"• Отказы: {m['bounce']}%\n"
        f"• Глубина: {m['depth']} стр.\n"
        f"• Время: {m['duration']} сек."
    )


def cmd_vkpost():
    send("📸 Публикую пост в ВК (случайная модель)...")
    vk_daily_post(random_product=True)


# ─── ВК СТАТЬЯ ───

import xml.etree.ElementTree as ET
import random as rnd

BRAND_WIKI = {
    "Armani Exchange":    "Armani",
    "Hide & Jack":        "Hide_%26_Jack",
    "Hugo BOSS":          "Hugo_Boss",
    "Brunello Cucinelli": "Brunello_Cucinelli",
    "Prada":              "Prada",
}

# RSS-источники: живые новости моды и кроссовок
RSS_SOURCES = [
    ("Hypebeast",     "https://hypebeast.com/feed"),
    ("Sneaker News",  "https://sneakernews.com/feed/"),
    ("Highsnobiety",  "https://www.highsnobiety.com/feed/"),
    ("Footwear News", "https://footwearnews.com/feed/"),
]

HEADERS_RSS = {
    "User-Agent": "Mozilla/5.0 (compatible; KickLuxeBot/1.0)"
}


def fetch_rss_item(brand_name):
    """Ищет свежую новость о бренде или моде в RSS-лентах."""
    search_terms = [brand_name.lower().split()[0], "sneaker", "luxury", "fashion", "style"]
    rnd.shuffle(RSS_SOURCES)

    for source_name, url in RSS_SOURCES:
        try:
            r = requests.get(url, headers=HEADERS_RSS, timeout=10)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            items = root.findall(".//item")
            rnd.shuffle(items)

            for item in items[:20]:
                title = (item.findtext("title") or "").lower()
                desc  = (item.findtext("description") or "")
                # Убираем HTML-теги из описания
                desc_clean = desc.replace("<p>", "").replace("</p>", " ")
                for tag in ["<b>","</b>","<i>","</i>","<br/>","<br>"]:
                    desc_clean = desc_clean.replace(tag, "")
                text = (title + " " + desc_clean).lower()

                if any(t in text for t in search_terms):
                    raw_title = item.findtext("title") or ""
                    return source_name, raw_title, desc_clean[:800]

            # Если по бренду нет — берём любую свежую новость о моде/кроссовках
            for item in items[:5]:
                raw_title = item.findtext("title") or ""
                desc  = (item.findtext("description") or "")[:800]
                return source_name, raw_title, desc

        except Exception as e:
            print(f"RSS {source_name} error: {e}")
            continue

    return None, None, None


def fetch_reddit_sneakers():
    """Берёт горячие посты из r/Sneakers на Reddit (JSON API, без ключей)."""
    try:
        r = requests.get(
            "https://www.reddit.com/r/Sneakers/hot.json?limit=10",
            headers=HEADERS_RSS,
            timeout=10
        )
        if r.status_code != 200:
            return None, None
        posts = r.json()["data"]["children"]
        rnd.shuffle(posts)
        for post in posts[:10]:
            d = post["data"]
            if not d.get("is_self") and d.get("score", 0) > 100:
                return d.get("title", ""), d.get("selftext", "")[:600]
    except Exception as e:
        print(f"Reddit error: {e}")
    return None, None


def fetch_wiki_facts(brand_name):
    """Факты о бренде из Википедии — запасной источник."""
    wiki_name = BRAND_WIKI.get(brand_name, brand_name.replace(" ", "_"))
    for lang in ("ru", "en"):
        try:
            r = requests.get(
                f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{wiki_name}",
                timeout=8
            )
            if r.status_code == 200:
                extract = r.json().get("extract", "")
                if len(extract) > 100:
                    return extract[:1000]
        except Exception:
            pass
    return ""


def gather_context(brand_name):
    """Собирает контекст из лучшего доступного источника."""
    # Случайно выбираем источник для разнообразия
    source_priority = rnd.choices(
        ["rss", "reddit", "wiki"],
        weights=[50, 30, 20],
        k=1
    )[0]

    if source_priority == "rss":
        src, title, desc = fetch_rss_item(brand_name)
        if title:
            return "rss", src, f"Заголовок: {title}\n{desc}"

    if source_priority == "reddit":
        title, text = fetch_reddit_sneakers()
        if title:
            return "reddit", "r/Sneakers", f"Обсуждение: {title}\n{text}"

    # Fallback — попробовать оба оставшихся
    src, title, desc = fetch_rss_item(brand_name)
    if title:
        return "rss", src, f"Заголовок: {title}\n{desc}"

    title, text = fetch_reddit_sneakers()
    if title:
        return "reddit", "r/Sneakers", f"Обсуждение: {title}\n{text}"

    wiki = fetch_wiki_facts(brand_name)
    if wiki:
        return "wiki", "Wikipedia", wiki

    return "none", "", ""


def vk_article_post(random_product=False):
    """Публикует статью-пост на основе свежих новостей моды."""
    print(f"[{datetime.datetime.now():%H:%M}] ВК статья...")
    try:
        if random_product:
            product = rnd.choice(PRODUCTS)
        else:
            day_num = datetime.date.today().toordinal()
            product = PRODUCTS[day_num % len(PRODUCTS)]

        name   = product["name"]
        photos = product["photos"]

        # Собираем контекст из интернета
        src_type, src_name, context = gather_context(name)

        if src_type == "rss":
            context_block = f"\n\nСвежая новость из {src_name}:\n{context}"
            instruction = "Используй эту новость как вдохновение — перескажи идею на русском, адаптируй под аудиторию ВК"
        elif src_type == "reddit":
            context_block = f"\n\nЧто обсуждают любители кроссовок прямо сейчас ({src_name}):\n{context}"
            instruction = "Используй эту тему как отправную точку для интересного поста"
        elif src_type == "wiki":
            context_block = f"\n\nФакты о бренде:\n{context}"
            instruction = "Используй эти факты чтобы рассказать интересную историю"
        else:
            context_block = ""
            instruction = "Придумай интересный угол — тренды, стиль, советы по выбору обуви"

        prompt = f"""Напиши увлекательный пост-статью ВКонтакте для магазина KickLuxe (продаём реплики премиум кроссовок).

Бренд в фокусе: {name}{context_block}

Задача: {instruction}

Требования:
- 5-7 предложений, живо и интересно — как пишут модные блогеры
- НЕ пиши "оригинал", "настоящая кожа", "оригинальное качество"
- Плавно упомяни что похожие модели в стиле {name} есть в KickLuxe
- В конце: "Смотрите на kickluxe.ru или пишите нам!"
- 5 хэштегов: #KickLuxe #кроссовки #мода #{name.replace(' ', '')} #стиль
- Начни с яркого эмодзи и цепляющего заголовка
- Пиши сразу пост, без предисловий"""

        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )

        if resp.status_code != 200:
            send(f"❌ Ошибка Claude при статье: {resp.status_code}")
            return

        post_text = resp.json()["content"][0]["text"]

        photo_idx  = datetime.date.today().toordinal() % len(photos)
        attachment = photos[photo_idx]

        vk_params = {
            "owner_id":   f"-{VK_GROUP_ID}",
            "from_group": 1,
            "message":    post_text,
            "attachments": attachment,
            "access_token": VK_TOKEN,
            "v": "5.199"
        }
        r = requests.post("https://api.vk.com/method/wall.post", data=vk_params, timeout=15)
        result = r.json()

        if "response" in result:
            post_id = result["response"]["post_id"]
            send(
                f"📰 Статья опубликована в ВК!\n"
                f"vk.com/kickluxe?w=wall-{VK_GROUP_ID}_{post_id}\n\n"
                f"Источник: {src_name or 'Claude'} | Бренд: {name}"
            )
        else:
            send(f"❌ Ошибка публикации статьи: {result.get('error', {}).get('error_msg', str(result))}")

    except Exception as e:
        send(f"❌ Ошибка статьи: {e}")
        print(f"vk_article_post error: {e}")


def cmd_vkarticle():
    send("📰 Пишу статью с интересными фактами...")
    threading.Thread(target=lambda: vk_article_post(random_product=True)).start()


def cmd_wordstat():
    send("📈 Запускаю Вордстат анализ — займёт 30-60 секунд...")
    threading.Thread(target=wordstat_weekly).start()


def cmd_help():
    send(
        "🤖 KickLuxe Bot\n\n"
        "/report — отчёт прямо сейчас\n"
        "/optimize — ИИ анализирует и применяет изменения\n"
        "/status — статус кампании\n"
        "/vkpost — пост с фото в ВК (случайная модель)\n"
        "/vkarticle — статья с фактами о бренде в ВК\n"
        "/wordstat — расширить ключи через Вордстат\n"
        "/help — эта подсказка\n\n"
        f"📅 Расписание:\n"
        f"• Отчёт + оптимизация — каждый день {REPORT_HOUR:02d}:00\n"
        f"• Пост с фото — каждый день {VK_POST_HOUR:02d}:00\n"
        f"• Статья с фактами — Пн/Ср/Пт в 15:00\n"
        f"• Вордстат — воскресенье 10:00"
    )


# ─── ПЛАНИРОВЩИК ───

def scheduler_loop():
    schedule.every().day.at(f"{REPORT_HOUR:02d}:00").do(cmd_optimize)
    schedule.every().day.at(f"{VK_POST_HOUR:02d}:00").do(vk_daily_post)
    schedule.every().monday.at("15:00").do(vk_article_post)
    schedule.every().wednesday.at("15:00").do(vk_article_post)
    schedule.every().friday.at("15:00").do(vk_article_post)
    schedule.every().sunday.at("10:00").do(wordstat_weekly)
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
        "/vkpost — пост с фото в ВК (случайная модель)\n"
        "/vkarticle — статья с фактами о бренде в ВК\n"
        "/wordstat — расширить ключи через Вордстат\n"
        "/help — все команды и расписание"
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
                elif text.startswith("/vkarticle"):
                    cmd_vkarticle()
                elif text.startswith("/wordstat"):
                    cmd_wordstat()
                elif text.startswith("/help") or text.startswith("/start"):
                    cmd_help()
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()

