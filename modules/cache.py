import json
import os
import os.path
import threading
import time
import sqlite3

from modules.paths import data_path, script_path
from modules import shared

cache_filename = os.environ.get('SD_WEBUI_CACHE_FILE', os.path.join(data_path, "cache.json"))
cache_db_path = os.environ.get('SD_WEBUI_CACHE_DATABASE', os.path.join(data_path, "cache_database.db"))
cache_data = None
cache_lock = threading.Lock()

dump_cache_after = None
dump_cache_thread = None


def cache_db_to_dict(db_path):
    try:
        database_dict = {}
        with sqlite3.connect(db_path) as conn:
            for table in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                table_name = table[0]
                table_data = conn.execute(f"SELECT * FROM `{table_name}`").fetchall()
                database_dict[table_name] = {row[0]: {"mtime": row[1], "value": json.loads(row[2])} for row in
                                             table_data}
        return database_dict
    except Exception as e:
        print(e)
        return {}


def dump_cache():
    """
    Marks cache for writing to disk. 5 seconds after no one else flags the cache for writing, it is written.
    """

    global dump_cache_after
    global dump_cache_thread

    def thread_func():
        global dump_cache_after
        global dump_cache_thread

        while dump_cache_after is not None and time.time() < dump_cache_after:
            time.sleep(1)

        with cache_lock:
            cache_filename_tmp = cache_filename + "-"
            with open(cache_filename_tmp, "w", encoding="utf8") as file:
                json.dump(cache_data, file, indent=4)

            os.replace(cache_filename_tmp, cache_filename)

            dump_cache_after = None
            dump_cache_thread = None

    with cache_lock:
        dump_cache_after = time.time() + 5
        if dump_cache_thread is None:
            dump_cache_thread = threading.Thread(name='cache-writer', target=thread_func)
            dump_cache_thread.start()


def cache(subsection):
    """
    Retrieves or initializes a cache for a specific subsection.

    Parameters:
        subsection (str): The subsection identifier for the cache.

    Returns:
        dict: The cache data for the specified subsection.
    """

    global cache_data

    if shared.opts.experimental_sqlite_cache:
        if cache_data is None:
            with cache_lock:
                if cache_data is None:
                    cache_data = cache_db_to_dict(cache_db_path)
        s = cache_data.get(subsection, {})
        if not s:
            try:
                with cache_lock:
                    with sqlite3.connect(cache_db_path) as conn:
                        conn.execute(
                            f'CREATE TABLE IF NOT EXISTS `{subsection}` (path TEXT PRIMARY KEY, mtime REAL, value TEXT)')
            except Exception as e:
                print(e)
        cache_data[subsection] = s
        return s

    if cache_data is None:
        with cache_lock:
            if cache_data is None:
                if not os.path.isfile(cache_filename):
                    cache_data = {}
                else:
                    try:
                        with open(cache_filename, "r", encoding="utf8") as file:
                            cache_data = json.load(file)
                    except Exception:
                        os.replace(cache_filename, os.path.join(script_path, "tmp", "cache.json"))
                        print(
                            '[ERROR] issue occurred while trying to read cache.json, move current cache to tmp/cache.json and create new cache')
                        cache_data = {}

    s = cache_data.get(subsection, {})
    cache_data[subsection] = s

    return s


def cached_data_for_file(subsection, title, filename, func, func_message: str = None):
    """
    Retrieves or generates data for a specific file, using a caching mechanism.

    Parameters:
        subsection (str): The subsection of the cache to use.
        title (str): The title of the data entry in the subsection of the cache.
        filename (str): The path to the file to be checked for modifications.
        func (callable): A function that generates the data if it is not available in the cache.
        func_message (str): when non-blank, prints {func_message}{func()} if func is called
    Returns:
        dict or None: The cached or generated data, or None if data generation fails.

    The `cached_data_for_file` function implements a caching mechanism for data stored in files.
    It checks if the data associated with the given `title` is present in the cache and compares the
    modification time of the file with the cached modification time. If the file has been modified,
    the cache is considered invalid and the data is regenerated using the provided `func`.
    Otherwise, the cached data is returned.

    If the data generation fails, None is returned to indicate the failure. Otherwise, the generated
    or cached data is returned as a dictionary.
    """

    existing_cache = cache(subsection)
    ondisk_mtime = os.path.getmtime(filename)

    entry = existing_cache.get(title)
    if entry:
        cached_mtime = entry.get("mtime", 0)
        if ondisk_mtime != cached_mtime:
            entry = None

    if not entry or 'value' not in entry:
        if func_message:
            print(f"{func_message}", end="")
        value = func()
        if func_message:
            print(value)
        if value is None:
            return None

        if shared.opts.experimental_sqlite_cache:
            try:
                with cache_lock:
                    with sqlite3.connect(cache_db_path) as conn:
                        insert_or_replace = f"INSERT OR REPLACE INTO `{subsection}` (path, mtime, value) VALUES (?, ?, ?)"
                        conn.execute(insert_or_replace, (title, ondisk_mtime, json.dumps(value)))
                        existing_cache[title] = {'mtime': ondisk_mtime, 'value': value}
                    return value
            except Exception as e:
                print(e)
                return None

        entry = {'mtime': ondisk_mtime, 'value': value}
        existing_cache[title] = entry

        dump_cache()

    return entry['value']
