import argparse
import sys
import datetime
import zipfile
import hashlib
import json
import uuid
import logging
import os
import re
import html
import gzip
import shutil

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

def gzip_file(input_path):
    with open(input_path, 'rb') as f_in:
        with open(f"{input_path}.gz", 'wb') as f_gz:
            with gzip.GzipFile(fileobj=f_gz, mode='wb', compresslevel=9, mtime=0) as f_out:
                shutil.copyfileobj(f_in, f_out)

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
    parser.add_argument("-r", "--archive-dir", help="Путь к каталогу с архивами")
    parser.add_argument("-w", "--work-dir", help="Путь к рабочему каталогу")
    parser.add_argument("-a", "--authors-path", help="Путь к файлу авторов")
    parser.add_argument("-c", "--catalog-path", help="Путь к файлу каталога")
    parser.add_argument("-l", "--log", help="Файл протокола")
    parser.add_argument("-i", "--ignore-history", action="store_true", help="Не загружать историю")
    parser.add_argument("-s", "--skip-existing", action="store_true", help="Пропускать обработанные книги")
    parser.add_argument("-u", "--store-unknown", action="store_true", help="Хранить книги без авторов")
    parser.add_argument("-m", "--store-metadata", action="store_true", help="Хранить данные о книгах")
    parser.add_argument("-t", "--test-mode", action="store_true", help="Не сохранять новые книги")
    parser.add_argument("-g", "--no-gzip", action="store_true", help="Не cжимать словари")
    args = parser.parse_args()

    ignore_history = args.ignore_history
    skip_existing = args.skip_existing
    store_unknown = args.store_unknown
    store_metadata = args.store_metadata
    test_mode = args.test_mode
    no_gzip = args.no_gzip

    program_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    archive_dir = args.archive_dir or input("Введите путь к каталогу с архивами: ").strip().strip('"')
    work_dir = args.work_dir or input("Введите путь к рабочему каталогу: ").strip().strip('"')
    authors_path = args.authors_path or os.path.join(work_dir, "authors.json")
    catalog_path = args.catalog_path or os.path.join(work_dir, "catalog.json")
    index_path = os.path.join(work_dir, "index.html")
    favicon_path = os.path.join(work_dir, "favicon.svg")
    log = args.log or os.path.join(program_dir, "process.log")

    if not os.path.exists(work_dir):
        os.makedirs(work_dir)

    set_logger(log)

    terminal_width = 80

    logging.info(f"Запуск сценария сортировки архива библиотеки Librusec")
    logging.info("-" * terminal_width)
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
            logging.info(f"Загружены настройки из файла \"{settings_file_path}\"")
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

    # допустимые языки
    lang_list = {}
    if settings["lang_list"]:
        lang_list = [lang.strip() for lang in settings["lang_list"].split(',')]

    # сжатие
    compress = settings["compress"].split(":")
    compresstype = compress[0].strip()
    if not compresstype or compresstype == "none":
        compresstype = "none"
        logging.warning("Тип сжатия не установлен")
    elif compresstype in ("zip", "gzip", "gz", "bzip2", "bz2", "lzma", "xz"):
        try:
            compresslevel = int(compress[1].strip()) if len(compress) > 1 else 5 
        except Exception as e:
            logging.error(f"Не удалось установить степень сжатия: {e}")
            return

        if compresstype == "gz": compresstype = "gzip"
        if compresstype == "bz2": compresstype = "bzip2"
        if compresstype == "xz": compresstype = "lzma"
    elif compresstype in ("7zip", "7z"):
        try:
            import py7zr
        except ImportError:
            logging.error(f"Библиотека поддержки формата \"{compresstype}\" не установлена (pip install py7zr)")
            return

        if compresstype == "7zip": compresstype = "7z"
    else:
        logging.error(f"Тип сжатия \"{compresstype}\" не поддерживается")
        return

    compressext = {
        "zip": ".zip", 
        "gzip": ".gz",
        "bzip2": ".bz2",
        "lzma": ".xz",
        "7z": ".7z"
    }

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

    # загружаем базу авторов
    authors = {}
    if os.path.exists(authors_path):
        try:
            with open(authors_path, 'r', encoding='utf-8') as authors_file:
                for line in authors_file:
                    if line.strip():
                        authors.update(json.loads(line.lower()))
            logging.info(f"Имеющихся авторов: {len(authors)}")
        except Exception as e:
            logging.error(f"Не удалось прочитать файл со списком авторов \"{authors_path}\": {e}")
            return

    # загружаем имена обработанных ранее книг
    existing_books = set()
    if os.path.exists(catalog_path):
        try:
            with open(catalog_path, 'r', encoding='utf-8') as catalog_file:
                for line in catalog_file:
                    data = json.loads(line)
                    for file_name in data:
                        if file_name:
                            existing_books.add(file_name)
            logging.info(f"Имеющихся книг: {len(existing_books)}")
        except Exception as e:
            logging.error(f"Не удалось прочитать файл со списком книг \"{catalog_path}\": {e}")
            return

    # получение перечня архивов
    zip_list = []
    for root, dirs, files in os.walk(archive_dir):
        for current_file in files:
            if current_file.lower().endswith('.zip'):
                zip_list.append(os.path.join(root, current_file))

    if not zip_list:
        logging.warning(f"В каталоге \"{archive_dir}\" и его подкаталогах нет zip-архивов")

    all_file_count = 0
    all_new_file_count = 0

    authors_changed = False
    catalog_changed = False

    # перебор найденных архивов
    for zip_path in zip_list:
        zip_name = os.path.basename(zip_path)

        stats = os.stat(zip_path)
        archive_key = f"{zip_name}:{stats.st_size}:{stats.st_mtime}"

        # обработанные ранее архивы пропускаем
        if archive_key in history and not ignore_history:
            logging.info(f"\"{zip_name}\" уже был обработан ранее")
            continue

        logging.info(f">>> Обработка архива \"{zip_name}\"...")

        try:
            file_ext = settings["format"]
            file_ext = "." + file_ext if not file_ext.startswith(".") else file_ext

            file_count = 0
            new_file_count = 0

            with zipfile.ZipFile(zip_path, 'r') as zip:
                file_list = [f for f in zip.namelist() if f.lower().endswith(file_ext)]
                logging.info(f"Количество файлов {settings['format']} в архиве: {len(file_list)}")

                for file_name in file_list:
                    file_count += 1
                    file_state = "новый файл"

                    # имя целевого файла
                    if compresstype == "none":
                        book_file_name = os.path.basename(file_name)
                    elif compresstype == "zip" or compresstype == "7z":
                        book_file_name = os.path.splitext(os.path.basename(file_name))[0] + compressext.get(compresstype, "")
                    else:
                        book_file_name = os.path.splitext(os.path.basename(file_name))[0] + file_ext + compressext.get(compresstype, "")

                    if not skip_existing or book_file_name not in existing_books:
                        # попытка определить данные о книге
                        encoding, lang, author, title, genre, version, date, annotation = get_book_info(zip, file_name)

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
                                with open(authors_path, 'a', encoding='utf-8') as authors_file:
                                    authors_file.write(
                                        json.dumps(
                                            {author: uid}, 
                                            ensure_ascii=False
                                        ) 
                                        + 
                                        "\n"
                                    )
                                authors_changed = True
                            else:
                                uid = authors[author.lower()]
                        else:
                            uid = "0"
                            author = "Неизвестен"
                        
                        if (lang in lang_list or not lang or not lang_list) and (uid != "0" or store_unknown):
                            author_dir = os.path.join(work_dir, settings["books_subdir"], uid)
                            os.makedirs(author_dir, exist_ok=True)

                            book_file_path = os.path.join(author_dir, book_file_name)

                            # копирование/переупаковка файла
                            book_file_exists = os.path.exists(book_file_path)
                            if not test_mode and not book_file_exists:
                                with zip.open(file_name) as archived_data:
                                    source_file = archived_data.read()
                                if compresstype == "zip":
                                    with zipfile.ZipFile(book_file_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=compresslevel) as target_file:
                                        target_file.writestr(os.path.basename(file_name), source_file)
                                elif compresstype == "gzip":
                                    with gzip.open(book_file_path, 'wb', compresslevel=compresslevel) as target_file:
                                        target_file.write(source_file)
                                elif compresstype == "bzip2":
                                    with bz2.open(book_file_path, 'wb', compresslevel=compresslevel) as target_file:
                                        target_file.write(source_file)
                                elif compresstype == "lzma":
                                    with lzma.open(book_file_path, 'wb', preset=compresslevel) as target_file:
                                        target_file.write(source_file)
                                elif compresstype == "7z":
                                    with py7zr.SevenZipFile(book_file_path, 'w') as target_file:
                                        target_file.writestr(source_file, os.path.basename(file_name))
                                elif compresstype == "none":
                                    with open(book_file_path, 'wb') as target_file:
                                        target_file.write(source_file)

                                book_file_exists = True
                                new_file_count += 1
                            else:
                                file_state = "уже есть"

                            # сохраняем данные о книге
                            if store_metadata and book_file_exists:
                                metadata_path = os.path.join(work_dir, settings["metadata_subdir"], uid)
                                os.makedirs(metadata_path, exist_ok=True)
                                metadata_file_path = os.path.join(metadata_path, file_name.split('.')[0] + ".json")
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
                                            ensure_ascii=False, 
                                            indent=0
                                        )

                            # добавляем книгу в базу
                            if book_file_name not in existing_books and book_file_exists:
                                existing_books.add(book_file_name)
                                with open(catalog_path, 'a', encoding='utf-8') as catalog_file:
                                    catalog_file.write(
                                        json.dumps(
                                            {book_file_name: [uid, create_index(author, title), os.path.getsize(book_file_path)]}, 
                                            ensure_ascii=False
                                        ) 
                                        + 
                                        "\n"
                                    )
                                catalog_changed = True
                        else:
                            file_state = "пропущен"

                        logging.info(f"{file_count}: <{lang or 'N/A'}> {uid} [{author}] <- \"{book_file_name}\" ({file_state}) <- \"{file_name}\"")
                    else:
                        file_state = "обработан ранее"
                        logging.info(f"{file_count}: \"{file_name}\" ({file_state})")
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

    # web-оглавление
    try:
        shutil.copyfile(os.path.join(program_dir, "index.html"), index_path)
    except Exception as e:
        logging.warning(f"Не удалось скопировать index.html: {e}")
    # web-иконка
    try:
        shutil.copyfile(os.path.join(program_dir, "favicon.svg"), favicon_path)
    except Exception as e:
        logging.warning(f"Не удалось скопировать favicon.svg: {e}")

    # сжатие файлов для web
    if not no_gzip:
        if (authors_changed or not os.path.exists(authors_path + ".gz")): gzip_file(authors_path)
        if (catalog_changed or not os.path.exists(catalog_path + ".gz")): gzip_file(catalog_path)
        if os.path.exists(index_path): gzip_file(index_path)

if __name__ == "__main__":
    main()
