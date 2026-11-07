import html
import pathlib

RELOC = {
    "yes": "релокация указана",
    "remote": "удалённо",
    "onsite_no_info": "офис, про релокацию не сказано",
    "unknown": "локация не указана",
    "no": "нет",
}


def _row(j):
    return (
        j["company"],
        j["title"],
        j.get("location") or "—",
        RELOC.get(j.get("relocation", "unknown"), j.get("relocation", "")),
        j.get("reason", "") + ("" if j.get("judged") else " (без оценки ИИ)"),
        j["url"],
    )


def build(today, open_fit, new_urls, failed, html_only, stats, coverage=(), contacts=()):
    """Return (markdown, html). open_fit: list of job dicts that fit and are open."""
    new = [j for j in open_fit if j["url"] in new_urls]
    md = [f"# Подходящие вакансии — {today}", ""]
    md.append(
        f"Открытых подходящих: **{len(open_fit)}**, новых сегодня: **{len(new)}**. "
        f"Проверено компаний: {stats['ok']} из {stats['total']}."
    )
    for title, rows in (("Новые сегодня", new), ("Все открытые подходящие", open_fit)):
        md += ["", f"## {title}", ""]
        if not rows:
            md.append("Нет.")
            continue
        md += ["| Компания | Роль | Локация | Релокация | Почему подходит | Ссылка |", "|---|---|---|---|---|---|"]
        for j in rows:
            c, t, loc, rel, why, url = _row(j)
            md.append(f"| {c} | {t} | {loc} | {rel} | {why} | [открыть]({url}) |")
    md += ["", "## Не удалось прочитать", ""]
    md += [f"- {n}: {why}" for n, why in failed] or ["Нет."]
    md += ["", "## Подача резюме без списка вакансий (почта, общая форма)", ""]
    if contacts:
        md += ["| Компания | Почта | Ссылка | Заметка |", "|---|---|---|---|"]
        for c in contacts:
            note = ("✅ заявка уже отправлена. " if c.get("applied") else "") + c.get("note", "")
            link = f"[открыть]({c['url']})" if c.get("url") else "—"
            md.append(f"| {c['name']} | {', '.join(c.get('emails', [])) or '—'} | {link} | {note} |")
    else:
        md.append("Нет.")
    md += ["", "## Покрытие по компаниям", "", "| Компания | Способ чтения | Вакансий найдено | QA-подобных |", "|---|---|---|---|"]
    md += [f"| {n} | {m} | {t} | {q} |" for n, m, t, q in sorted(coverage)]
    md += [
        "",
        "## Прочитаны только как обычная HTML-страница",
        "Страницы на JavaScript и закрытые формы могут не отдавать вакансии; для надёжности добавьте прямую ссылку в `config/overrides.json`.",
        "",
    ]
    md += [f"- {n}" for n in html_only] or ["Нет."]

    def table(rows):
        if not rows:
            return "<p>Нет.</p>"
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(x)}</td>" for x in _row(j)[:5])
            + f'<td><a href="{html.escape(j["url"])}">открыть</a></td></tr>'
            for j in rows
        )
        return (
            "<table><tr><th>Компания</th><th>Роль</th><th>Локация</th><th>Релокация</th>"
            f"<th>Почему подходит</th><th>Ссылка</th></tr>{body}</table>"
        )

    page = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Вакансии {today}</title>"
        "<style>body{font:15px system-ui;margin:2rem auto;max-width:1100px;padding:0 1rem}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}"
        "th{background:#f4f4f4}</style>"
        f"<h1>Подходящие вакансии — {today}</h1>"
        f"<p>Открытых: <b>{len(open_fit)}</b>, новых сегодня: <b>{len(new)}</b>. Проверено компаний: {stats['ok']} из {stats['total']}.</p>"
        f"<h2>Новые сегодня</h2>{table(new)}<h2>Все открытые подходящие</h2>{table(open_fit)}"
        f"<h2>Подача резюме без списка вакансий</h2><ul>{''.join('<li>' + html.escape(c['name']) + ': ' + html.escape(', '.join(c.get('emails', [])) or '-') + ' ' + html.escape(c.get('note', '')) + '</li>' for c in contacts) or '<li>Нет.</li>'}</ul>"
        f"<h2>Не удалось прочитать</h2><ul>{''.join(f'<li>{html.escape(n)}: {html.escape(w)}</li>' for n, w in failed) or '<li>Нет.</li>'}</ul>"
    )
    return "\n".join(md) + "\n", page


def write(root: pathlib.Path, md: str, page: str, today: str):
    (root / "reports").mkdir(exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "reports" / "latest.md").write_text(md, encoding="utf-8")
    (root / "reports" / f"{today}.md").write_text(md, encoding="utf-8")
    (root / "docs" / "index.html").write_text(page, encoding="utf-8")
