# DjVu to PDF for macOS

Небольшое macOS-приложение для пакетной конвертации DjVu в компактный PDF с поиском по тексту, закладками и сохранением цветных страниц.

Проект распространяется на условиях GNU GPL версии 3. Полный текст находится в [LICENSE](LICENSE); сведения о сторонних компонентах — в [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Возможности

- Очередь файлов: drag-and-drop нескольких документов и папок, последовательная конвертация и отмена.
- Профиль **«Авто»**: цветные страницы сохраняются в JPEG RGB, страницы в оттенках серого — в JPEG grayscale, а монохромные страницы — в JBIG2.
- Профиль **«Чистый ч/б (JBIG2)»**: для всей страницы создаётся 1-битное изображение. Цвет и оттенки серого при этом теряются.
- Скрытый Unicode-текстовый слой для поиска и копирования; перенос вложенных закладок DjVu.
- Превью первой страницы, выбор каталога вывода и уведомление о завершении очереди.
- Открытие `.djvu`/`.djv` через Finder и сборка автономного `.app` и `.dmg` для macOS.

## Требования

- macOS
- Python 3.10+
- Homebrew-утилиты DjVuLibre и jbig2enc для запуска из исходников:

```sh
brew install djvulibre jbig2enc
```

Приложение находит `ddjvu`, `djvused`, `djvudump` и `jbig2` в `PATH` и стандартных каталогах Homebrew. Собранное приложение включает эти бинарники и необходимые библиотеки.

Исходный код DjVuLibre: [репозиторий проекта](https://github.com/DjVuLibre/djvulibre) и [архивы исходников](https://sourceforge.net/projects/djvu/files/DjVuLibre/). При сборке и распространении приложения вместе с DjVuLibre соблюдайте условия его лицензии и сохраняйте соответствующий исходный код.

## Установка и запуск из исходников

```sh
git clone https://github.com/Aillight/compact-djvu2pdf.git
cd compact-djvu2pdf
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python src/main.py
```

PDF по умолчанию сохраняется рядом с исходником. Если файл с таким именем уже существует, приложение запрашивает подтверждение перед перезаписью.

## Тесты

```sh
source .venv/bin/activate
pytest -q
```

Интеграционный тест с реальным DjVu-файлом выполняется только при наличии соответствующего fixture и установленного `ddjvu`; пользовательские документы в репозиторий не включаются.

## Сборка macOS-приложения

Сборку выполняйте на macOS после установки зависимостей Python и Homebrew-утилит:

```sh
python scripts/build_macos_app.py
```

Результат: `dist/djvu2pdf.app`. Для проверки включённых бинарников:

```sh
dist/djvu2pdf.app/Contents/MacOS/djvu2pdf --check-tools
```

Можно запустить конвертацию через исполняемый файл собранного приложения:

```sh
dist/djvu2pdf.app/Contents/MacOS/djvu2pdf \
  --convert input.djvu output.pdf \
  --profile auto
```

Доступны профили `auto` и `clean-black-and-white`; если `--profile` не указан, используется `auto`.

Чтобы создать и проверить установочный образ:

```sh
python scripts/create_dmg.py
```

Результат: `dist/djvu2pdf-macOS.dmg`.

Локальная сборка подписывается ad-hoc и не проходит нотариализацию Apple. Для публичного релиза, который будет запускаться без предупреждений Gatekeeper на других компьютерах, потребуется Developer ID-подпись и нотариализация.

## Диагностика размера PDF

Скрипт сравнивает структуру DjVu и потоки изображений в готовом PDF:

```sh
PYTHONPATH=src python scripts/audit_compression.py input.djvu output.pdf
```

## GitHub-описание

**Краткое описание:** `macOS GUI app for converting DjVu books to compact, searchable PDFs with JBIG2 compression.`

**Topics:** `macos`, `djvu`, `pdf`, `pyside6`, `python`, `jbig2`, `ebook`
