import argparse
import sys
import datetime
import zipfile
import hashlib
import json
import uuid
import logging
import os
import io
import re
import html
import gzip
import shutil
import collections

def gzip_file(file_name):
    if os.path.exists(file_name):
        with open(file_name, 'rb') as f_in:
            with open(f"{file_name}.gz", 'wb') as f_gz:
                with gzip.GzipFile(fileobj=f_gz, mode='wb', compresslevel=9, mtime=0) as f_out:
                    shutil.copyfileobj(f_in, f_out)

def rotate_file(file_name, ext, depth=10):
    if os.path.exists(file_name):
        if not ext.startswith("."):
            ext = "." + ext

        last = f"{file_name}{ext}.{depth - 1}"
        if os.path.exists(last):
            os.remove(last)

        for i in range(depth - 1, 2, -1):
            old = f"{file_name}{ext}.{i - 1}"
            new = f"{file_name}{ext}.{i}"
            if os.path.exists(old):
                os.rename(old, new)

        current = f"{file_name}{ext}"
        if os.path.exists(current):
            os.rename(current, f"{current}.2")

        os.rename(file_name, current)

def set_logger(log):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.handlers = []
    formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler = logging.FileHandler(log, mode='a', encoding='utf-8')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    console_formatter = logging.Formatter('%(message)s')
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    return logger

def replace_ext(file_name, ext, count=1):
    if not ext.startswith("."):
        ext = "." + ext
    for i in range(count):
        file_name = os.path.splitext(file_name)[0]

    return os.path.splitext(file_name)[0] + ext

def smart_truncate(text, max_len=4096):
    if len(text) <= max_len:
        return text

    limit = max_len - 3
    truncated = text[:limit]
    last_space = truncated.rfind(' ')
    if last_space != -1:
        truncated = truncated[:last_space]
    return truncated.rstrip('.,!?; ') + '...'

def create_index(author, title):
    # слова автора для фильтрации (в нижнем регистре)
    author_words = set(re.findall(r'\w+', author.lower()))

    # разбивка названия по пробелам
    title_words_orig = title.split()

    filtered_title = []
    for word in title_words_orig:
        # очищаем слово от знаков препинания для проверки
        clean_word = re.sub(r'[^\w]', '', word).lower()

        if clean_word not in author_words and clean_word != "":
            filtered_title.append(word)

    # cклеиваем автора и очищенный заголовок
    return f"{author} {' '.join(filtered_title)}"

def get_book_uid(file_name, format_ext):
    book_file_name = os.path.basename(file_name).lower()
    book_uid = os.path.splitext(book_file_name)[0]
    if book_uid.endswith(format_ext) and book_file_name.endswith((".zip", ".gz", ".bz2", ".xz", ".7z")):
        book_uid = os.path.splitext(book_uid)[0]
    return book_uid

def get_book_info(zip, file_name):
    search_flags = re.DOTALL | re.IGNORECASE

    encoding = ""
    lang = ""
    author = ""
    title = ""
    genre = ""
    version = ""
    date = ""
    annotation = ""

    try:
        with zip.open(file_name) as f:
            # читаем начало файла
            raw_data = f.read(16384)

            # определяем кодировку
            encoding_match = re.search(rb'encoding=["\'](.*?)["\']', raw_data)
            if encoding_match:
                encoding = encoding_match.group(1).decode('ascii')

            if encoding:
                text = raw_data.decode(encoding, errors='ignore')
            else:
                text = raw_data.decode('utf-8', errors='ignore')

            # ищем блок <lang>
            lang_match = re.search(r'<lang>(.*?)</lang>', text, search_flags)
            if lang_match:
                lang = html.unescape(re.sub(r'<.*?>', '', lang_match.group(1)).strip())

            # ищем блоки <author>
            author_blocks = re.findall(r'<author>(.*?)</author>', text, search_flags)
            for author_block in author_blocks:
                # извлекаем имя, фамилию и отчество (опционально)
                first_name = re.search(r'<first-name>(.*?)</first-name>', author_block, search_flags)
                last_name = re.search(r'<last-name>(.*?)</last-name>', author_block, search_flags)
                middle_name = re.search(r'<middle-name>(.*?)</middle-name>', author_block, search_flags)

                # очистка от тегов и лишних пробелов
                name_parts = []
                if first_name:
                    name_parts.append(re.sub(r'<.*?>', '', first_name.group(1)).strip()[:256])
                if middle_name:
                    name_parts.append(re.sub(r'<.*?>', '', middle_name.group(1)).strip()[:256])
                if last_name:
                    name_parts.append(re.sub(r'<.*?>', '', last_name.group(1)).strip()[:256])
                # фильтруем пустые части и объединяем
                author = html.unescape(" ".join([p for p in name_parts if p]))

                if author:
                    break

            # ищем блок <book-title>
            title_match = re.search(r'<book-title>(.*?)</book-title>', text, search_flags)
            if title_match:
                title = html.unescape(re.sub(r'<.*?>', '', title_match.group(1)).strip()[:256])
                title = " ".join(title.split()) if title else ""

            if not title:
                # ищем блок <title>
                title_match = re.search(r'<title>(.*?)</title>', text, search_flags)
                if title_match:
                    title = html.unescape(re.sub(r'<.*?>', '', title_match.group(1)).strip()[:256])
                    title = " ".join(title.split()) if title else ""

            # ищем блок <genre>
            genre_match = re.search(r'<genre>(.*?)</genre>', text, search_flags)
            if genre_match:
                genre = html.unescape(re.sub(r'<.*?>', '', genre_match.group(1)).strip()[:128])

            # ищем блок <version>
            version_match = re.search(r'<version>(.*?)</version>', text, search_flags)
            if version_match:
                version = html.unescape(re.sub(r'<.*?>', '', version_match.group(1)).strip()[:8])

            # ищем блок <date>
            date_match = re.search(r'<date>(.*?)</date>', text, search_flags)
            if date_match:
                date = html.unescape(re.sub(r'<.*?>', '', date_match.group(1)).strip()[:32])

            # ищем блок <annotation>
            annotation_match = re.search(r'<annotation>(.*?)</annotation>', text, search_flags)
            if annotation_match:
                annotation = html.unescape(re.sub(r'<.*?>', '', annotation_match.group(1)).strip()[:8192])
                annotation = smart_truncate(" ".join(annotation.split())) if annotation else ""
    except:
        pass

    return encoding, lang, author, title, genre, version, date, annotation

def main():
    parser = argparse.ArgumentParser(description="Сценарий сортировки архива библиотеки Librusec")
    parser.add_argument("-w", "--work-dir", help="Путь к рабочему каталогу")
    parser.add_argument("-r", "--archive-dir", help="Путь к каталогу с архивами")
    parser.add_argument("-a", "--authors-path", help="Путь к файлу авторов")
    parser.add_argument("-c", "--catalog-path", help="Путь к файлу каталога")
    parser.add_argument("-l", "--log", help="Файл протокола")
    parser.add_argument("-d", "--delete-before", help="Удалять отсутствующие книги")
    parser.add_argument("-e", "--clean-empty", action="store_true", help="Очищать от пустых каталогов")
    parser.add_argument("-i", "--ignore-history", action="store_true", help="Не загружать историю")
    parser.add_argument("-s", "--skip-existing", action="store_true", help="Пропускать обработанные книги")
    parser.add_argument("-u", "--store-unknown", action="store_true", help="Хранить книги без авторов")
    parser.add_argument("-m", "--store-metadata", action="store_true", help="Хранить данные о книгах")
    parser.add_argument("-p", "--repack", action="store_true", help="Переупаковывать уже сжатые")
    parser.add_argument("-t", "--test-mode", action="store_true", help="Не сохранять новые книги")
    parser.add_argument("-g", "--use-gzip", action="store_true", help="Использовать gzip для web")
    args = parser.parse_args()

    program_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

    work_dir = args.work_dir or input("Введите путь к рабочему каталогу: ").strip().strip('"')
    archive_dir = args.archive_dir or input("Введите путь к каталогу с архивами: ").strip().strip('"')
    authors_path = args.authors_path or os.path.join(work_dir, "authors.json")
    catalog_path = args.catalog_path or os.path.join(work_dir, "catalog.json")
    index_path = os.path.join(work_dir, "index.html")
    favicon_path = os.path.join(work_dir, "favicon.svg")
    log = args.log or os.path.join(program_dir, "process.log")
    delete_before = args.delete_before
    clean_empty = args.clean_empty
    ignore_history = args.ignore_history
    skip_existing = args.skip_existing
    store_unknown = args.store_unknown
    store_metadata = args.store_metadata
    repack = args.repack
    test_mode = args.test_mode
    use_gzip = args.use_gzip

    if not os.path.exists(work_dir):
        os.makedirs(work_dir)

    set_logger(log)

    terminal_width = 80

    logging.info(f"Запуск сценария сортировки архива библиотеки Librusec")
    logging.info("-"*terminal_width)
    for parser_action in parser._actions:
        if parser_action.dest != "help":
            parser_value = getattr(args, parser_action.dest)
            parser_description = parser_action.help
            if parser_action.dest == "authors_path": parser_value = authors_path
            if parser_action.dest == "catalog_path": parser_value = catalog_path
            if parser_action.dest == "log": parser_value = log
            if parser_description:
                logging.info(f"{parser_description:<32}: \"{parser_value}\"")
    logging.info("-"*terminal_width)

    if delete_before and delete_before not in ("demo", "rename", "purge"):
        logging.warning(f"Режим очистки \"{delete_before}\" не поддерживается")
        return

    if not os.path.exists(archive_dir):
        logging.error(f"Каталог \"{archive_dir}\" не найден")
        return

    # загружаем настройки
    settings = {}
    settings_file_path = os.path.join(program_dir, "settings.json")
    if os.path.exists(settings_file_path):
        try:
            with open(settings_file_path, "r", encoding="utf-8") as settings_file:
                settings = json.load(settings_file)
            logging.info(f"Получены настройки из файла \"{settings_file_path}\"")
        except Exception as e:
            logging.error(f"Не удалось прочитать файл \"{settings_file_path}\": {e}")
            return
    else:
        logging.warning(f"Отсутствует файл настроек \"{settings_file_path}\", будут использованы значения по-умолчанию")

    settings["format"] = settings.get("format", "fb2").lower()
    settings["lang_list"] = settings.get("lang_list", "ru").lower()
    settings["compress"] = settings.get("compress", "zip:5")
    settings["save_history"] = settings.get("save_history", False)
    settings["books_subdir"] = settings.get("books_subdir", "books")
    settings["metadata_subdir"] = settings.get("metadata_subdir", "metadata")
    settings["author_ident"] = settings.get("author_ident", "guid")

    format_ext = settings["format"]
    format_ext = "." + format_ext if not format_ext.startswith(".") else format_ext

    archive_ext = ".zip"
    backup_ext = ".bak"

    # допустимые языки
    langs = set()
    if settings["lang_list"]:
        try:
            for item in settings["lang_list"].split(','):
                lang = item.lower().strip()
                if lang:
                    langs.add(lang)
            if langs:
                langs_filter = ", ".join(sorted(langs))
                logging.info(f"Фильтр по языкам: {langs_filter}")
        except Exception as e:
            logging.error(f"Не удалось установить фильтр по языкам: {e}")
            return

    # сжатие
    compress = settings["compress"].split(":")
    compresstype = compress[0].strip()
    compresslevel = None
    if compresstype == "":
        pass
    elif compresstype in ("zip", "gzip", "gz", "bzip2", "bz2", "lzma", "xz"):
        try:
            compresslevel = int(compress[1].strip()) if len(compress) > 1 else 5
        except Exception as e:
            logging.error(f"Не удалось установить степень сжатия: {e}")
            return
    elif compresstype in ("7zip", "7z"):
        try:
            import py7zr
        except ImportError:
            logging.error(f"Библиотека поддержки формата \"{compresstype}\" не установлена (pip install py7zr)")
            return
    else:
        logging.error(f"Тип сжатия \"{compresstype}\" не поддерживается")
        return

    if compresstype:
        if compresstype == "gzip": compresstype = "gz"
        if compresstype == "bzip2": compresstype = "bz2"
        if compresstype == "lzma": compresstype = "xz"
        if compresstype == "7zip": compresstype = "7z"

        if compresslevel is not None:
            logging.info(f"Установлено сжатие \"{compresstype}\", уровень {compresslevel}")
        else:
            logging.info(f"Установлено сжатие \"{compresstype}\"")
    else:
        logging.warning("Сжатие не установлено")

    # жанры
    genres = {}
    genres_file_path = os.path.join(program_dir, "genres.json")
    if os.path.exists(genres_file_path):
        try:
            with open(genres_file_path, "r", encoding="utf-8") as genres_file:
                genres = json.load(genres_file)
            logging.info(f"Загружены жанры из файла \"{genres_file_path}\"")
        except Exception as e:
            logging.error(f"Не удалось прочитать файл жанров \"{genres_file_path}\": {e}")
            return
    else:
        logging.warning(f"Отсутствует файл жанров \"{genres_file_path}\"")

    # загружаем историю обработанных архивов
    history = {}
    history_path = os.path.join(program_dir, "history.json")
    if not ignore_history:
        if os.path.exists(history_path):
            try:
                with open(history_path, 'r', encoding='utf-8') as history_file:
                    for line in history_file:
                        if line.strip():
                            history.update(json.loads(line))
                logging.info(f"Обработанных ранее архивов: {len(history)}")
            except Exception as e:
                logging.error(f"Не удалось прочитать файл истории \"{history_path}\": {e}")
                return

    # загружаем словарь авторов
    authors = {}
    if os.path.exists(authors_path):
        try:
            with open(authors_path, 'r', encoding='utf-8') as dictionary:
                for line in dictionary:
                    if line.strip():
                        authors.update(json.loads(line.lower()))
            logging.info(f"Имеющихся авторов: {len(authors)}")
        except Exception as e:
            logging.error(f"Не удалось прочитать словарь авторов \"{authors_path}\": {e}")
            return

    # загружаем каталог книг
    book_uids = set()
    book_file_names = set()
    if os.path.exists(catalog_path):
        try:
            with open(catalog_path, 'r', encoding='utf-8') as catalog:
                for line in catalog:
                    data = json.loads(line)
                    for file_name in data:
                        if file_name:
                            book_uids.add(get_book_uid(file_name, format_ext))
                            book_file_names.add(file_name.lower())
            logging.info(f"Имеющихся книг: {len(book_uids)} ({len(book_file_names)} файлов)")
        except Exception as e:
            logging.error(f"Не удалось прочитать каталог книг \"{catalog_path}\": {e}")
            return

    # исключения
    exclusions_file_path = os.path.join(program_dir, "exclusions.txt")
    if os.path.exists(exclusions_file_path):
        try:
            with open(exclusions_file_path, 'r') as exclusions_file:
                exclusions = {line.strip() for line in exclusions_file if line.strip()}
            logging.info(f"Исключенных книг: {len(exclusions)}")
        except Exception as e:
            logging.error(f"Не удалось прочитать файл исключений \"{exclusions_file_path}\": {e}")
            return

    # получение перечня архивов
    zip_list = []
    logging.info(f"Поиск архивов (\"*{archive_ext}\") в \"{archive_dir}\"...")
    for root, dirs, files in os.walk(archive_dir):
        for current_file in files:
            if current_file.lower().endswith(archive_ext):
                zip_list.append(os.path.join(root, current_file))
    if zip_list:
        logging.info(f"Обнаружено архивов: {len(zip_list)}")
    else:
        logging.warning(f"В каталоге \"{archive_dir}\" и его подкаталогах нет архивов")

    # очистка
    if zip_list and delete_before and os.path.exists(catalog_path):
        obsolete_uids = book_uids.copy()
        logging.info(f"Сравнение содержимого \"{catalog_path}\" и \"{archive_dir}\"...")

        # получение остатка книг, которых нет в источнике
        for zip_file_path in zip_list:
            with zipfile.ZipFile(zip_file_path, 'r') as zip:
                for file_name in (name for name in zip.namelist() if name.lower().endswith((format_ext, archive_ext))):
                    obsolete_uids.discard(get_book_uid(file_name, format_ext))

        if obsolete_uids:
            logging.info(f"Обнаружено {len(obsolete_uids)} книг, отсутствующих в \"{archive_dir}\"")

            yn = "[y/N]: "
            answer = "y"
            match delete_before:
                case "demo":
                    logging.info(f"В режиме delete-before={delete_before} удаление книг не производится")
                case "rename":
                    answer = input(f"Файлы лишних книг будут переименованы в \"*.bak\", продолжить? {yn}").lower().strip()
                case "purge":
                    answer = input(f"Файлы лишних книг будут удалены безвозвратно, продолжить? {yn}").lower().strip()

            if answer == "y" and delete_before in ("rename", "purge"):
                rotate_file(catalog_path, backup_ext)
                backup_catalog_path = catalog_path + backup_ext
                logging.info(f"Создана резервная копия (\"*{backup_ext}\") файла {catalog_path}")

                obsolete_book_file_count = 0
                obsolete_metadata_file_count = 0
                try:
                    with open(backup_catalog_path, 'r', encoding='utf-8') as catalog:
                        for line in catalog:
                            data = json.loads(line)
                            for file_name in data:
                                uid = get_book_uid(file_name, format_ext)
                                if uid in obsolete_uids:
                                    try:
                                        subdir = data[file_name][0]

                                        # книги
                                        file_kind = "файл книги"
                                        book_file_path = os.path.join(work_dir, settings["books_subdir"], subdir, file_name)
                                        if os.path.exists(book_file_path):
                                            match delete_before:
                                                case "rename":
                                                    rotate_file(book_file_path, backup_ext)
                                                    logging.info(f"Переименован {file_kind}: \"{book_file_path}\" -> \"{os.path.basename(book_file_path) + backup_ext}\"")
                                                case "purge":
                                                    os.remove(book_file_path)
                                                    logging.info(f"Удален {file_kind}: \"{book_file_path}\"")
                                        obsolete_book_file_count += 1

                                        # метаданные
                                        file_kind = "файл метаданных"
                                        if file_name.endswith((format_ext + ".gz", format_ext + ".bz2", format_ext +".xz")):
                                            ext_count = 2
                                        else:
                                            ext_count = 1
                                        metadata_file_path = os.path.join(work_dir, settings["metadata_subdir"], subdir, replace_ext(file_name, ".json", ext_count))
                                        if os.path.exists(metadata_file_path):
                                            match delete_before:
                                                case "rename":
                                                    rotate_file(metadata_file_path, backup_ext)
                                                    logging.info(f"Переименован {file_kind}: \"{metadata_file_path}\" -> \"{os.path.basename(metadata_file_path) + backup_ext}\"")
                                                case "purge":
                                                    os.remove(metadata_file_path)
                                                    logging.info(f"Удален {file_kind}: \"{book_file_path}\"")
                                        obsolete_metadata_file_count += 1

                                        book_uids.discard(uid)
                                        book_file_names.discard(file_name)
                                    except Exception as e:
                                        with open(catalog_path, 'a', encoding='utf-8') as catalog:
                                            catalog.write(line)
                                        logging.error(f"Не удалось выполнить очистку для uid=\"{uid}\": {e}")
                                else:
                                    with open(catalog_path, 'a', encoding='utf-8') as catalog:
                                        catalog.write(line)

                    logging.info(f"Очищено файлов книг: {obsolete_book_file_count})")
                    logging.info(f"Очищено файлов метаданных: {obsolete_metadata_file_count})")
                    logging.info(f"Осталось книг: {len(book_uids)} ({len(book_file_names)} файлов)")
                except Exception as e:
                    logging.error(f"Не удалось прочитать каталог книг \"{catalog_path}\": {e}")
                    return
            else:
                logging.info(f"Введено {answer}, удаление не будет выполнено")
        else:
            logging.info(f"Отсутствующих книг в \"{archive_dir}\" не обнаружено")

    # очистка от пустых папок
    if clean_empty:
        logging.info(f"Очистка от пустых папок в \"{work_dir}\"...")
        empty_dir_count = 0
        for root, dirs, files in os.walk(work_dir, topdown=False):
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                if not os.listdir(dir_path):
                    try:
                        os.rmdir(dir_path)
                        empty_dir_count += 1
                        logging.info(f"Удалена пустая папка: \"{dir_path}\"")
                    except Exception as e:
                        logging.warning(f"Ошибка при удалении пустой папки \"{dir_path}\": {e}")
        if empty_dir_count:
            logging.info(f"Всего удалено пустых папок в \"{work_dir}\": {empty_dir_count}")
        else:
            logging.info(f"Пустых папок в \"{work_dir}\" не найдено")

    all_file_count = 0
    all_new_file_count = 0

    used_langs = collections.Counter()
    skipped_langs = collections.Counter()
    no_langs = 0

    # перебор найденных архивов
    for zip_file_path in zip_list:
        zip_file_name = os.path.basename(zip_file_path)

        stats = os.stat(zip_file_path)
        archive_key = f"{zip_file_name}:{stats.st_size}:{stats.st_mtime}"

        # обработанные ранее архивы пропускаем
        if archive_key in history and not ignore_history:
            logging.info(f"\"{zip_file_name}\" уже был обработан ранее")
            continue

        logging.info(f">>> Обработка архива \"{zip_file_name}\"...")

        try:
            file_count = 0
            new_file_count = 0

            with zipfile.ZipFile(zip_file_path, 'r') as zip:
                file_list = zip.namelist()
                archive_capacity = len(file_list)
                logging.info(f"Количество файлов в архиве: {archive_capacity}")

                single_file_archive = archive_capacity == 1

                for file_name in file_list:
                    if not file_name.lower().endswith((format_ext, archive_ext)):
                        continue

                    current_zip = zip
                    current_file_name = file_name

                    nested = False
                    buffer = None

                    # если это вложенный архив с одним файлом
                    if file_name.lower().endswith(archive_ext):
                        buffer = io.BytesIO(zip.read(file_name))
                        try:
                            nested_zip = zipfile.ZipFile(buffer)
                            nested_file_list = nested_zip.namelist()
                            # в нем единственный файл с нужным расширением
                            if len(nested_file_list) == 1:
                                nested_file_name = nested_file_list[0]
                                # если имя архива равно имени вложенного файла, и это файл нужного формата
                                if nested_file_name.endswith(format_ext) and os.path.splitext(os.path.basename(nested_file_name))[0].lower() == os.path.splitext(os.path.basename(file_name))[0].lower():
                                    nested = True
                                    single_file_archive = True
                                    current_zip = nested_zip
                                    current_file_name = nested_file_name
                                else:
                                    nested_zip.close()
                                    buffer = None
                            else:
                                nested_zip.close()
                                buffer = None
                        except Exception as e:
                            logging.error(f"Не удалось прочитать вложенный архив \"{file_name}\": {e}")

                    try:
                        file_count += 1
                        file_state = "новый файл"

                        # uid книги
                        book_uid = get_book_uid(current_file_name, format_ext)

                        # определяем, надо ли в этот раз обрабатывать файл
                        skip = False
                        if book_uid in exclusions:
                            file_state = "исключен"
                            skip = True
                        if book_uid in book_uids and skip_existing:
                            file_state = "обработан ранее"
                            skip = True

                        # если надо, обрабатываем
                        if not skip:
                            # попытка определить данные о книге
                            encoding, lang, author, title, genre, version, date, annotation = get_book_info(current_zip, current_file_name)

                            # нормализация языка
                            lang = lang.lower()

                            # наименование жанра
                            genre = genres.get(genre, genre)

                            # если автор определен
                            if author:
                                # если новый автор, добавить в базу
                                if author.lower() not in authors:
                                    author_ident = settings["author_ident"]
                                    if author_ident == "guid":
                                        uid = uuid.uuid4().hex
                                    elif author_ident == "hash":
                                        uid = hashlib.md5(author.lower().encode('utf-8')).hexdigest()
                                    else:
                                        logging.error(f"Неизвестный тип UID: \"{author_ident}\"")
                                        return

                                    authors[author.lower()] = uid
                                    if not test_mode:
                                        with open(authors_path, 'a', encoding='utf-8') as dictionary:
                                            dictionary.write(json.dumps({author: uid}, ensure_ascii=False) + "\n")
                                else:
                                    uid = authors[author.lower()]
                            else:
                                uid = "0"
                                author = "Неизвестен"

                            # файл книги
                            book_file_name = os.path.basename(current_file_name).lower()
                            if compresstype == "zip" or compresstype == "7z":
                                book_file_name = replace_ext(book_file_name, compresstype)
                            elif compresstype:
                                book_file_name = book_file_name + replace_ext("", compresstype)
                            else:
                                pass
                            book_path = os.path.join(work_dir, settings["books_subdir"], uid)
                            book_file_path = os.path.join(book_path, book_file_name)

                            # файл метаданных
                            metadata_file_name =  replace_ext(os.path.basename(current_file_name).lower(), ".json")
                            metadata_path = os.path.join(work_dir, settings["metadata_subdir"], uid)
                            metadata_file_path = os.path.join(metadata_path, metadata_file_name)

                            if uid != "0" or store_unknown:
                                if lang in langs or not lang or not langs:
                                    if lang:
                                        used_langs[lang] += 1
                                    else:
                                        no_langs += 1
                                    # копирование/переупаковка файла
                                    if not test_mode and not os.path.exists(book_file_path):
                                        os.makedirs(book_path, exist_ok=True)

                                        # если в архиве только один файл, и нужен zip, то вместо повторного сжатия - копируем как есть
                                        if single_file_archive and compresstype == "zip" and not repack:
                                            if not nested:
                                                shutil.copyfile(zip_file_path, book_file_path)
                                            else:
                                                current_zip.fp.seek(0)
                                                with open(book_file_path, "wb") as book_file:
                                                    book_file.write(buffer.getbuffer())
                                            file_state = "готовый ZIP"
                                        else:
                                            with current_zip.open(current_file_name) as archived_data:
                                                if compresstype == "zip":
                                                    with zipfile.ZipFile(book_file_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=compresslevel) as target_file:
                                                        target_file.writestr(os.path.basename(current_file_name), archived_data.read())
                                                elif compresstype == "gzip":
                                                    with gzip.open(book_file_path, 'wb', compresslevel=compresslevel) as target_file:
                                                        target_file.write(archived_data.read())
                                                elif compresstype == "bzip2":
                                                    with bz2.open(book_file_path, 'wb', compresslevel=compresslevel) as target_file:
                                                        target_file.write(archived_data.read())
                                                elif compresstype == "lzma":
                                                    with lzma.open(book_file_path, 'wb', preset=compresslevel) as target_file:
                                                        target_file.write(archived_data.read())
                                                elif compresstype == "7z":
                                                    with py7zr.SevenZipFile(book_file_path, 'w') as target_file:
                                                        target_file.writestr(archived_data.read(), os.path.basename(current_file_name))
                                                elif compresstype == "":
                                                    with open(book_file_path, 'wb') as target_file:
                                                        target_file.write(archived_data.read())

                                        new_file_count += 1
                                    else:
                                        file_state = "уже есть"

                                    # сохраняем данные о книге
                                    if store_metadata and not os.path.exists(metadata_file_path):
                                        os.makedirs(metadata_path, exist_ok=True)
                                        if not os.path.exists(metadata_file_path):
                                            with open(metadata_file_path, 'w', encoding='utf-8') as metadata_file:
                                                json.dump(
                                                    {
                                                        "encoding": encoding,
                                                        "lang": lang,
                                                        "author": author,
                                                        "title": title,
                                                        "genre": genre,
                                                        "version": version,
                                                        "date": date,
                                                        "annotation": annotation
                                                    },
                                                    metadata_file,
                                                    ensure_ascii=False
                                                )

                                    # добавляем книгу в каталог
                                    if book_file_name not in book_file_names and os.path.exists(book_file_path):
                                        book_file_names.add(book_file_name)
                                        with open(catalog_path, 'a', encoding='utf-8') as catalog:
                                            catalog.write(
                                                json.dumps(
                                                    {book_file_name: [uid, create_index(author, title), os.path.getsize(book_file_path)]},
                                                    ensure_ascii=False
                                                )
                                                +
                                                "\n"
                                            )
                                    book_uids.add(book_uid)
                                else:
                                    if lang:
                                        skipped_langs[lang] += 1
                                    file_state = "пропущен: язык не из списка"
                            else:
                                file_state = "пропущен: не указан автор"

                            logging.info(f"{file_count}: <{lang or 'N/A'}> {uid} [{author}] <- \"{book_file_name}\" ({file_state}) <- \"{file_name}\"")
                        else:
                            logging.info(f"{file_count}: \"{current_file_name}\" ({file_state})")
                    finally:
                        if nested:
                            current_zip.close()
                            buffer = None

            # сохраняем состояние после каждого успешного архива
            history[archive_key] = datetime.datetime.now().isoformat()
            if settings["save_history"]:
                with open(history_path, 'a', encoding='utf-8') as history_file:
                    history_file.write(json.dumps({archive_key: history[archive_key]}, ensure_ascii=False) + '\n')

            logging.info(f"Готово (всего файлов: {file_count}, из них новых: {new_file_count})")

            all_file_count += file_count
            all_new_file_count += new_file_count
        except Exception as e:
            logging.error(f"Не удалось обработать архив: {e}")

    logging.info(f"Обработано архивов: {len(history)} (всего файлов: {all_file_count}, из них новых: {all_new_file_count})")

    if used_langs:
        langs_found = ", ".join([f"{lang} ({used_langs[lang]})" for lang in sorted(used_langs)])
        logging.info(f"Использовано языков: {langs_found}")
    if skipped_langs:
        langs_found = ", ".join([f"{lang} ({skipped_langs[lang]})" for lang in sorted(skipped_langs)])
        logging.info(f"Пропущено языков: {langs_found}")
    if no_langs:
        logging.info(f"Без языка: {no_langs}")

    # web-оглавление
    try:
        shutil.copyfile(os.path.join(program_dir, "index.html"), index_path)
    except Exception as e:
        logging.warning(f"Не удалось скопировать \"index.html\": {e}")
    # web-иконка
    try:
        shutil.copyfile(os.path.join(program_dir, "favicon.svg"), favicon_path)
    except Exception as e:
        logging.warning(f"Не удалось скопировать \"favicon.svg\": {e}")

    # сжатие файлов для web
    if use_gzip:
        gzip_file(authors_path)
        gzip_file(catalog_path)
        gzip_file(index_path)

if __name__ == "__main__":
    main()
