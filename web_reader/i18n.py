"""Тексты интерфейса и отчётов на трёх языках: русский, английский, украинский."""
from __future__ import annotations

# ключ: (ru, en, uk)
_TEXTS: dict[str, tuple[str, str, str]] = {
    # --- проверка соединений ---
    "preflight_title": ("Проверка перед запуском", "Pre-flight check", "Перевірка перед запуском"),
    "check_settings": ("Настройки", "Settings", "Налаштування"),
    "check_browser": ("Запуск браузера", "Browser launch", "Запуск браузера"),
    "check_render": ("Чтение JavaScript, iframe и shadow DOM", "JavaScript, iframe and shadow DOM reading", "Читання JavaScript, iframe та shadow DOM"),
    "check_network": ("Выход в интернет", "Internet access", "Вихід в інтернет"),
    "check_ok": ("в порядке", "OK", "у порядку"),
    "check_fail": ("ОШИБКА", "FAILED", "ПОМИЛКА"),
    "preflight_passed": ("Проверка пройдена, запускаю чтение.", "Check passed, starting the run.", "Перевірку пройдено, запускаю читання."),
    "preflight_failed": ("Проверка не пройдена, основной запуск отменён.", "Check failed, the main run is cancelled.", "Перевірку не пройдено, основний запуск скасовано."),
    # --- ход запуска ---
    "run_start": ("Запуск {run_id}: адресов {n}", "Run {run_id}: {n} address(es)", "Запуск {run_id}: адрес {n}"),
    "reading": ("Читаю: {url}", "Reading: {url}", "Читаю: {url}"),
    "run_done": ("Готово. Отчёт: {path}", "Done. Report: {path}", "Готово. Звіт: {path}"),
    "no_urls": ("Не указано ни одного адреса.", "No addresses given.", "Не вказано жодної адреси."),
    "bad_line": ("строка {n}: неверный адрес «{text}»", "line {n}: invalid address \"{text}\"", "рядок {n}: неправильна адреса «{text}»"),
    # --- статусы ---
    "status_ok": ("прочитано полностью", "read completely", "прочитано повністю"),
    "status_partial": ("прочитано частично", "read partially", "прочитано частково"),
    "status_blocked": ("закрыто защитой от ботов", "blocked by bot protection", "закрито захистом від ботів"),
    "status_error": ("ошибка открытия", "failed to open", "помилка відкриття"),
    "status_empty": ("открылась, но содержимого нет", "opened but no content", "відкрилася, але вмісту немає"),
    "status_skipped": ("пропущено", "skipped", "пропущено"),
    # --- заголовки отчёта ---
    "report_title": ("Отчёт чтения страницы", "Page reading report", "Звіт читання сторінки"),
    "f_url": ("Адрес", "URL", "Адреса"),
    "f_final_url": ("Конечный адрес", "Final URL", "Кінцева адреса"),
    "f_title": ("Заголовок", "Title", "Заголовок"),
    "f_status": ("Статус", "Status", "Статус"),
    "f_kind": ("Тип страницы", "Page type", "Тип сторінки"),
    "f_http": ("HTTP-код", "HTTP code", "HTTP-код"),
    "f_time": ("Время, с", "Time, s", "Час, с"),
    "f_mode": ("Режим", "Mode", "Режим"),
    "f_depth": ("Глубина", "Depth", "Глибина"),
    "f_relation": ("Как найдена", "How found", "Як знайдено"),
    "s_completeness": ("Полнота чтения", "Reading completeness", "Повнота читання"),
    "s_jobs": ("Найденные вакансии", "Vacancies found", "Знайдені вакансії"),
    "s_popups": ("Всплывающие окна", "Popups", "Спливні вікна"),
    "s_expansion": ("Раскрытие содержимого", "Content expansion", "Розкриття вмісту"),
    "s_frames": ("Встроенные окна (iframe)", "Embedded frames (iframe)", "Вбудовані вікна (iframe)"),
    "s_network": ("Данные, подгруженные страницей", "Data loaded by the page", "Дані, підвантажені сторінкою"),
    "s_text": ("Текст страницы", "Page text", "Текст сторінки"),
    "s_links": ("Ссылки", "Links", "Посилання"),
    "s_children": ("Страницы, открытые по ссылкам", "Pages opened via links", "Сторінки, відкриті за посиланнями"),
    "s_limit_sites": ("Сайты, где не хватило лимита (можно поднять для конкретного сайта)", "Sites that ran out of limit (can be raised per site)", "Сайти, де не вистачило ліміту (можна підняти для окремого сайту)"),
    "limit_site_hint": ("чтобы поднять, добавьте в config/settings.json раздел site_overrides", "to raise it, add a site_overrides section to config/settings.json", "щоб підняти, додайте до config/settings.json розділ site_overrides"),
    "s_roles": ("Подходящие по ролям", "Matching roles", "Підходящі за ролями"),
    "s_roles_text": ("Упомянуто в тексте вакансии (слабое совпадение)", "Mentioned in the posting text (weak match)", "Згадано в тексті вакансії (слабке збігання)"),
    "s_roles_excluded": ("Подходят по роли, но исключены по региону", "Matching role but excluded by region", "Підходять за роллю, але виключені за регіоном"),
    "roles_none": ("Подходящих вакансий в прочитанном не найдено.", "No matching vacancies found in what was read.", "Підходящих вакансій у прочитаному не знайдено."),
    "roles_disclaimer": ("Отбор идёт только по прочитанному: при частично прочитанных сайтах (раздел «Непрочитанное») подходящие вакансии могут быть не найдены.",
                         "Matching covers only what was read: for partially read sites (see the unread section) matching vacancies may be missing.",
                         "Добір іде лише за прочитаним: для частково прочитаних сайтів (розділ «Непрочитане») підходящі вакансії можуть бути не знайдені."),
    "s_unread": ("Непрочитанное и прочитанное не полностью", "Unread and partially read", "Непрочитане та прочитане не повністю"),
    "s_all": ("Все адреса", "All addresses", "Усі адреси"),
    "s_not_processed": ("Не обработано", "Not processed", "Не оброблено"),
    "s_input_problems": ("Проблемы во входных данных", "Input problems", "Проблеми у вхідних даних"),
    "s_settings_warnings": ("Предупреждения по настройкам", "Settings warnings", "Попередження щодо налаштувань"),
    "limits_hit": ("Сработавшие лимиты", "Limits hit", "Спрацьовані ліміти"),
    "warnings": ("Предупреждения", "Warnings", "Попередження"),
    "notes": ("Примечания", "Notes", "Примітки"),
    "declared": ("Заявлено на странице", "Declared on the page", "Заявлено на сторінці"),
    "collected": ("Собрано", "Collected", "Зібрано"),
    "none": ("нет", "none", "немає"),
    "complete_ok": ("Лимиты не срабатывали, предупреждений нет.", "No limits were hit and there are no warnings.", "Ліміти не спрацьовували, попереджень немає."),
    "col_title": ("Название", "Title", "Назва"),
    "col_location": ("Место", "Location", "Місце"),
    "col_url": ("Ссылка", "URL", "Посилання"),
    "col_source": ("Источник", "Source", "Джерело"),
    "col_text": ("Текст ссылки", "Link text", "Текст посилання"),
    "col_class": ("Тип", "Type", "Тип"),
    "col_decision": ("Что сделано", "Action", "Що зроблено"),
    "col_where": ("Куда ведёт", "Leads to", "Куди веде"),
    "col_status": ("Статус", "Status", "Статус"),
    "col_children": ("Открыто по ссылкам", "Opened via links", "Відкрито за посиланнями"),
    "truncated_text": ("Текст в отчёте обрезан, полный текст в файле JSON.", "Text truncated in the report; the full text is in the JSON file.", "Текст у звіті обрізано, повний текст у файлі JSON."),
    "more_items": ("…и ещё {n}", "…and {n} more", "…і ще {n}"),
    "screenshot": ("Снимок экрана", "Screenshot", "Знімок екрана"),
    "footer_generated": ("Сформировано web_reader {version}", "Generated by web_reader {version}", "Сформовано web_reader {version}"),
    "stop_reason": ("Причина остановки раскрытия", "Expansion stop reason", "Причина зупинки розкриття"),
    "scrolls": ("прокруток", "scrolls", "прокруток"),
    "expansions": ("раскрытий", "expansions", "розкриттів"),
    "dismissed": ("закрыто", "dismissed", "закрито"),
    "obstructions": ("остались помехи", "remaining obstructions", "залишились перешкоди"),
    # --- названия лимитов ---
    "lim_timeout": ("время на страницу", "page time limit", "час на сторінку"),
    "lim_hard_timeout": ("принудительная остановка по времени", "hard time stop", "примусова зупинка за часом"),
    "lim_max_pagination_pages": ("число страниц списка (пагинация)", "list pages limit (pagination)", "кількість сторінок списку (пагінація)"),
    "lim_max_pages_per_start": ("число страниц на один стартовый адрес", "pages per start address", "кількість сторінок на одну стартову адресу"),
    "lim_max_scrolls": ("число прокруток", "scroll limit", "кількість прокруток"),
    "lim_max_expansions": ("число раскрытий", "expansion limit", "кількість розкриттів"),
    "lim_max_jobs_per_page": ("число вакансий на страницу", "vacancies per page", "кількість вакансій на сторінку"),
    "lim_max_text_chars": ("объём текста", "text size", "обсяг тексту"),
    "lim_max_requests_per_page": ("число сетевых запросов страницы", "page network request limit", "кількість мережевих запитів сторінки"),
    "lim_max_frames": ("число встроенных окон", "frame limit", "кількість вбудованих вікон"),
    "lim_navigation_timeout": ("загрузка страницы не завершилась вовремя", "page load did not finish in time", "завантаження сторінки не завершилось вчасно"),
    "lim_navigated_away": ("клик увёл с исходной страницы", "a click navigated away from the page", "клік відвів зі сторінки"),
    "lim_max_json_responses": ("число перехваченных JSON-ответов", "captured JSON response limit", "кількість перехоплених JSON-відповідей"),
    # --- предупреждения ---
    "w_declared_gt_collected": (
        "Страница заявляет {declared} вакансий, собрано {collected}: возможна неполнота.",
        "The page declares {declared} vacancies, {collected} collected: possibly incomplete.",
        "Сторінка заявляє {declared} вакансій, зібрано {collected}: можлива неповнота."),
    "w_declared_low": (
        "Возможное число вакансий на странице: {declared} (низкая уверенность), собрано {collected}.",
        "Possible vacancy count on the page: {declared} (low confidence), {collected} collected.",
        "Можлива кількість вакансій на сторінці: {declared} (низька впевненість), зібрано {collected}."),
    "w_no_jobs": (
        "Страница похожа на раздел вакансий, но вакансий не обнаружено.",
        "The page looks like a careers page but no vacancies were detected.",
        "Сторінка схожа на розділ вакансій, але вакансій не виявлено."),
    "w_obstruction": (
        "Осталось окно, закрывающее страницу: {text}",
        "A window still covers the page: {text}",
        "Залишилось вікно, що закриває сторінку: {text}"),
    "w_antibot": (
        "Страница закрыта защитой от ботов ({detail}). Обход не выполняется.",
        "The page is blocked by bot protection ({detail}). No bypass is attempted.",
        "Сторінку закрито захистом від ботів ({detail}). Обхід не виконується."),
    "w_retry_recovered": (
        "Первая попытка получила отказ, повторное чтение удалось (попытка {attempt}).",
        "The first attempt was denied; the retry succeeded (attempt {attempt}).",
        "Перша спроба отримала відмову, повторне читання вдалося (спроба {attempt})."),
    "w_retry_failed": (
        "Сайт отказал и при повторных попытках ({attempts}). Защита не обходилась.",
        "The site denied access on retries too ({attempts}). No bypass was attempted.",
        "Сайт відмовив і при повторних спробах ({attempts}). Захист не обходився."),
    "w_empty": (
        "Страница открылась, но почти не содержит текста.",
        "The page opened but contains almost no text.",
        "Сторінка відкрилась, але майже не містить тексту."),
    "w_closed_posting": (
        "Похоже, вакансия закрыта или недоступна.",
        "The posting appears to be closed or unavailable.",
        "Схоже, вакансію закрито або вона недоступна."),
    "w_text_truncated": (
        "Текст обрезан по лимиту объёма.",
        "Text truncated by the size limit.",
        "Текст обрізано за лімітом обсягу."),
    "w_capped_jobs": (
        "Найдено больше вакансий, чем лимит ({limit}); показана часть.",
        "More vacancies found than the limit ({limit}); only part is listed.",
        "Знайдено більше вакансій, ніж ліміт ({limit}); показано частину."),
    "w_frame_unreadable": (
        "Встроенное окно не удалось прочитать: {url}",
        "An embedded frame could not be read: {url}",
        "Вбудоване вікно не вдалося прочитати: {url}"),
    "n_presentation": (
        "Найдена встроенная презентация ({url}); её содержимое может быть не извлечено.",
        "An embedded presentation was found ({url}); its content may not be extracted.",
        "Знайдено вбудовану презентацію ({url}); її вміст може бути не витягнуто."),
    "n_new_tab": (
        "Страница пыталась открыть новую вкладку: {url}",
        "The page tried to open a new tab: {url}",
        "Сторінка намагалась відкрити нову вкладку: {url}"),
    "n_dialog": (
        "Страница показала системное окно: {text}",
        "The page showed a system dialog: {text}",
        "Сторінка показала системне вікно: {text}"),
    "n_blocked_requests": (
        "Заблокировано запросов к рекламе и трекерам: {n}",
        "Blocked requests to ads and trackers: {n}",
        "Заблоковано запитів до реклами та трекерів: {n}"),
    "n_bad_json": (
        "Часть JSON-ответов не удалось прочитать: {n}",
        "Some JSON responses could not be read: {n}",
        "Частину JSON-відповідей не вдалося прочитати: {n}"),
    "n_shadow_closed": (
        "Закрытые shadow DOM прочитать нельзя; если текста мало, это возможная причина.",
        "Closed shadow DOM cannot be read; if text is scarce this may be the reason.",
        "Закриті shadow DOM прочитати не можна; якщо тексту мало, це можлива причина."),
    # --- типы страниц ---
    "kind_job_posting": ("вакансия", "job posting", "вакансія"),
    "kind_job_listing": ("список вакансий", "job listing", "список вакансій"),
    "kind_closed_posting": ("вакансия закрыта или недоступна", "closed or unavailable posting", "вакансія закрита або недоступна"),
    "kind_not_found": ("страница не найдена", "page not found", "сторінку не знайдено"),
    "kind_blocked": ("защита от ботов", "bot protection", "захист від ботів"),
    "kind_login": ("требуется вход", "login required", "потрібен вхід"),
    "kind_pdf": ("PDF-документ", "PDF document", "PDF-документ"),
    "kind_download": ("загружаемый файл", "downloadable file", "файл для завантаження"),
    "kind_other": ("обычная страница", "regular page", "звичайна сторінка"),
    "kind_error": ("не открылась", "failed to open", "не відкрилась"),
    # --- следование по ссылкам ---
    "d_read_full": ("открыта и прочитана полностью", "opened and read fully", "відкрито та прочитано повністю"),
    "d_read_light": ("открыта и прочитана", "opened and read", "відкрито та прочитано"),
    "d_pdf": ("PDF скачан, текст извлечён", "PDF downloaded, text extracted", "PDF завантажено, текст витягнуто"),
    "d_probe": ("быстрая проверка без браузера", "quick check without a browser", "швидка перевірка без браузера"),
    "d_listed": ("только записана", "recorded only", "лише записано"),
    "d_skip_external": ("внешний сайт, не открывалась", "external site, not opened", "зовнішній сайт, не відкривалась"),
    "d_skip_social": ("соцсеть, не открывалась", "social network, not opened", "соцмережа, не відкривалась"),
    "d_skip_budget": ("не открыта: исчерпан лимит", "not opened: limit reached", "не відкрито: вичерпано ліміт"),
    "d_skip_depth": ("не открыта: предел глубины", "not opened: depth limit", "не відкрито: межа глибини"),
    "d_skip_dup": ("уже читалась", "already read", "уже читалась"),
    "d_skip_blocked": ("не открыта: запрещённый адрес", "not opened: forbidden address", "не відкрито: заборонена адреса"),
    "d_skip_type": ("не открывалась: тип не читается", "not opened: unsupported type", "не відкривалась: тип не читається"),
    "d_pagination": ("следующая страница списка, прочитана полностью", "next list page, read fully", "наступна сторінка списку, прочитана повністю"),
    "d_not_probed": ("не проверена: исчерпан лимит быстрых проверок", "not checked: quick-check limit reached", "не перевірена: вичерпано ліміт швидких перевірок"),
    "d_mail": ("почта или телефон", "mail or phone", "пошта або телефон"),
    "d_anchor": ("якорь на этой же странице", "anchor on the same page", "якір на цій самій сторінці"),
}

_INDEX = {"ru": 0, "en": 1, "uk": 2}


def t(lang: str, key: str, **kwargs: object) -> str:
    """Возвращает текст по ключу на нужном языке (при отсутствии — английский, затем ключ)."""
    entry = _TEXTS.get(key)
    if entry is None:
        return key
    text = entry[_INDEX.get(lang, 1)] or entry[1]
    return text.format(**kwargs) if kwargs else text


def all_keys() -> list[str]:
    return list(_TEXTS)
