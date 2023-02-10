#!/usr/bin/python3

import re, sys, os
import getopt
import time
import csv

import urllib.request
import xml.etree.ElementTree as ET

from collections import OrderedDict

# Downloads premium videos from Giantbomb.

# Requires a premium account API key placed in api_key.txt in the same directory
# as this script. Reportedly there is an undocumented limit of 100 videos
# downloaded per day (not abiding, still testing), and a documented max request
# rate of 200 requests per hour (abiding by this).

# Edit g_skip_titles to filter out shows from download list

################################################################################
# Globals 
################################################################################
g_gb_url = "https://www.giantbomb.com"
g_api_key = "" # To be initialized in main
g_dl_file = "dl.csv"
g_done_file = "done.csv"
g_error_file = "err.csv"
g_pbar = None
g_start_time = time.time()

g_dl_count = 0                          # Download count
g_rq_count = 0                          # Request count
g_query_limit = 100                     # Max amount of videos to query
g_max_dl_rate = 1000000000/(24*60*60)   # Max 100 videos per day (in videos/second)
g_max_rq_rate = 200/(60*60)             # Max 200 requests per hour (in requests/second)

# Skip queuing these titles for download
g_skip_titles = ["Giant Bombcast", "The Giant Beastcast"]

# Regex patterns
g_premium_page_pattern = re.compile("\s+<a href=\"(?P<url>/(?:shows|videos)/[/\-\w\d]+/(?P<guid>\d{2,6}\-\d{2,6})).*")
g_dl_url_pattern = None
g_publish_date_pattern = re.compile("([\d-]+) [\d:]+")
g_video_dl_name_pattern = re.compile(".*/(.*)\.mp4\s*")

################################################################################
# Main
################################################################################
def main(argv):
    # Init variables
    query_mode = True           # Queries for video download links
    download_mode = True        # Downloads videos
    dl_dict = OrderedDict()     # Dictionary of all the videos to download
    query_dict = OrderedDict()  # Temp dictionary to query videos to download
    done_dict = {}              # Dictionary of all videos already downloaded

    # Init api key
    try:
        global g_api_key
        with open("api_key.txt", "r") as api_file:
            g_api_key = api_file.read()
        if g_api_key == "":
            print("ERROR: Invalid API key from api_key.txt! Please paste your valid API key in there. Exiting...")
            return 1
    except:
        print("ERROR: Missing api_key.txt! Please create this file with only you API key in it. Exiting...")
        return 1

    # Init regex pattern
    global g_dl_url_pattern
    g_dl_url_pattern = re.compile("\s+<a href=\"(.*mp4\?api_key={})\"".format(g_api_key))

    # Parse arguments
    if len(sys.argv) != 0:
        try:
            opts, args = getopt.getopt(argv, "hqd", ["query", "download"])
        except getopt.GetoptError:
            print_usage()
            sys.exit(2)

        for opt, arg in opts:
            if opt == '-h':
                print_usage()
                sys.exit(0)
            elif opt in ('-q', '--query'):
                print("Query mode enabled, download mode disabled")
                query_mode = True
                download_mode = False
            elif opt in ('-d', '--download'):
                print("Download mode enabled; query mode disabled")
                download_mode = True
                query_mode = False

    # Helper function to get first item in an ordered dictionary
    def get_first_pair(d):
        return next(iter(d.items()))

    # Load any previous progress
    dl_dict, done_dict = load_progress()

    # Query mode
    if query_mode:
        # Query from API all premium videos
        print("Querying premium videos from API...")
        offset = 0
        
        while True:
            query_dict = get_dl_urls_from_api(offset, done_dict)
            if len(query_dict) == 0:
                break
            dl_dict.update(query_dict)

            # Write to progress files
            save_progress(dl_dict, done_dict)
            offset += g_query_limit

    # Download mode
    if download_mode:
        # For each video to download...
        while len(dl_dict) > 0:

            # Download video (FIFO) and cut it from list
            dl_name, dl_url = get_first_pair(dl_dict)
            if download_video(dl_name, dl_url):
                done_dict[dl_name] = dl_url
                del dl_dict[dl_name]
            else:
                # If download fails, put it in error progress file
                with open(g_error_file, "a", encoding="utf-8") as err_file:
                    err_file.write("\"{}\",\"{}\"\n".format(dl_name, dl_url))
                del dl_dict[dl_name]

            # Once video is done downloading, update the progress files
            save_progress(dl_dict, done_dict)

        # Delete empty dl progress file
        print("Done downloading all videos! Deleting {}...".format(g_dl_file))
        os.remove(g_dl_file)

    sys.exit(0)

################################################################################
# Desc
#   Gets video urls and guids from premium page. Currently unused.
# Params
#   page_no     int page number to grab urls/guids from
# Returns
#   url_list    str list of video urls
#   guid_list   str list of video guids
################################################################################
def get_url_list_from_page(page_no):
    print("Searching for premium URLs on page {}...".format(page_no))

    premium_url = g_gb_url + "/videos/premium/?page={0}".format(page_no)
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
    try:
        response = opener.open(premium_url)
        page_html = response.read().decode('utf-8')
        inc_and_check_rq_rate()
    except Exception as e:
        print(e)
        print("ERROR: Exception occurred during premium page {0} url fetch!".format(page_no))
        return None, None

    url_list = []
    guid_list = []
    page_html_lines = page_html.split('\n')
    for line in page_html_lines:
        match = g_premium_page_pattern.search(line)
        if match:
            premium_url = g_gb_url + match.group("url")
            guid = match.group("guid")
            url_list.append(premium_url)
            guid_list.append(guid)
            print("    {}\t{}".format(premium_url, guid))

    print("Found {} matches on page {}!".format(len(url_list), page_no))

    return url_list, guid_list

################################################################################
# Desc
#   Gets download urls and forms download name from videos API call.
#   All-in-one step vs calling get_url_list_from_page and get_dl_url_from_guid.
# Params
#   offset          int changes which set of videos are queried by API
#   done_dict       dict of completed downloads (dl_name, dl_url)
# Returns
#   query_dict      dict of videos gathered from query (dl_name, dl_url)
################################################################################
def get_dl_urls_from_api(offset, done_dict):
    global g_api_key
    filter = "premium:true"
    limit = 100 # Hard limit by API
    sort = "id:asc"
    field_list = ["name", "publish_date", "video_show", "hd_url", "high_url", "low_url"]
    
    # Query list of premium videos from API
    xml_url = "https://www.giantbomb.com/api/videos/?api_key={}&offset={}&filter={}&limit={}&sort={}&field_list={}".format(g_api_key, offset, filter, limit, sort, ','.join(field_list))
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
    try:
        response = opener.open(xml_url)
        xml = response.read().decode('utf-8')
        inc_and_check_rq_rate()
    except Exception as e:
        print(e)
        print("ERROR: Exception occurred during videos (offset {}) fetch!".format(offset))
        return None, None

    # Parse xml to find...
    #   <name>
    #   <publish_date>
    #   <video_show> --> <title>
    #   <hd_url>
    #   <high_url>
    #   <low_url>
    dl_url = ""
    dl_name = ""
    date = None
    raw_name = None

    root = ET.fromstring(xml)
    videos = root.findall('./results/video')
    
    # Gather data for each video
    query_dict = OrderedDict()
    for video in videos:
        pretty_name = video.find('./name')
        date_time = video.find('./publish_date')
        title = video.find('./video_show/title')
        hd_url = video.find('./hd_url')
        high_url = video.find('./high_url')
        low_url = video.find('./low_url')

        # Check for skip title
        if title is not None:
            if title.text in g_skip_titles:
                continue

        # Find highest quality download link
        if hd_url is not None and hd_url.text:
            dl_url = hd_url.text
        elif high_url is not None and high_url.text:
            dl_url = high_url.text
        elif low_url is not None and low_url.text:
            dl_url = low_url.text
        else:
            print("ERROR: Could not find valid download link from video!")
            return None, None

        # Strip down date_time to just date
        if date_time is not None:
            match = g_publish_date_pattern.search(date_time.text)
            if match:
                date = match.group(1)
            else:
                print("WARN: Could not get date from <publish_date> field!")
        else:
            print("WARN: No <publish_date> field found!")

        # Get raw name from hd/high/low_url
        match = g_video_dl_name_pattern.search(dl_url)
        if match:
            raw_name = match.group(1)
        else:
            print("WARN: Could not get raw name from <hd_url>/<high_url>/<low_url> field!")

        # Assemble the name of the download: {date}_{pretty_name}_{raw_name}.mp4
        if date:
            dl_name = "[{}]".format(date)
        if pretty_name is not None:
            # Remove any invalid characters
            invalid_chars = "<>:\"/\\|?*"
            clean_pretty_name = "".join(x for x in pretty_name.text if x not in invalid_chars)
            dl_name = "{}_[{}]".format(dl_name, clean_pretty_name)
        if raw_name:
            dl_name = "{}_[{}].mp4".format(dl_name, raw_name)
        else:
            dl_name = "{}.mp4".format(dl_name)

        if dl_name not in done_dict:
            query_dict[dl_name] = dl_url

    return query_dict

################################################################################
# Desc
#   Gets download url from guid, and what to name it
# Params
#   guid        str guid for video to get
# Returns
#   dl_name     str download name
#   dl_url      str download url
################################################################################
def get_dl_url_from_guid(guid):
    global g_api_key
    field_list = ["name", "publish_date", "hd_url", "high_url", "low_url"]

    # Query xml from website
    xml_url = "https://www.giantbomb.com/api/video/{}/?api_key={}&field_list={}".format(guid, g_api_key, ','.join(field_list))    
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
    try:
        response = opener.open(xml_url)
        xml = response.read().decode('utf-8')
        inc_and_check_rq_rate()
    except Exception as e:
        print(e)
        print("ERROR: Exception occurred during XML guid {} fetch!".format(guid))
        return None, None

    # Parse xml to find...
    #   <name>
    #   <publish_date>
    #   <hd_url>
    #   <high_url>
    #   <low_url>
    dl_url = ""
    dl_name = ""
    date = None
    raw_name = None

    root = ET.fromstring(xml)
    pretty_name = root.find('./results/name')
    date_time = root.find('./results/publish_date')
    hd_url = root.find('./results/hd_url')
    high_url = root.find('./results/high_url')
    low_url = root.find('./results/low_url')
    
    # Find highest quality download link
    if hd_url is not None and hd_url.text:
        dl_url = hd_url.text
    elif high_url is not None and high_url.text:
        dl_url = high_url.text
    elif low_url is not None and low_url.text:
        dl_url = low_url.text
    else:
        print("ERROR: Could not find valid download link from guid {}".format(guid))
        return None, None

    # Strip down date_time to just date
    if date_time is not None:
        match = g_publish_date_pattern.search(date_time.text)
        if match:
            date = match.group(1)
        else:
            print("WARN: Could not get date from <publish_date> field!")
    else:
        print("WARN: No <publish_date> field found!")

    # Get raw name from hd/high/low_url
    match = g_video_dl_name_pattern.search(dl_url)
    if match:
        raw_name = match.group(1)
    else:
        print("WARN: Could not get raw name from <hd_url>/<high_url>/<low_url> field!")

    # Assemble the name of the download: {date}_{pretty_name}_{raw_name}.mp4
    if date:
        dl_name = "[{}]".format(date)
    if pretty_name is not None:
        dl_name = "{}_[{}]".format(dl_name, pretty_name.text)
    if raw_name:
        dl_name = "{}_[{}].mp4".format(dl_name, raw_name)
    else:
        dl_name = "{}.mp4".format(dl_name)

    return dl_name, dl_url

################################################################################
# Desc
#   Downloads a video from given dl_url, and names it dl_name
# Params
#   dl_name     str to name the downloaded video
#   dl_url      str url to download from
# Returns
#   bool        True on success, otherwise False
################################################################################
def download_video(dl_name, dl_url):
    global g_api_key

    # Check if file already exists
    if os.path.exists("./{}".format(dl_name)):
        print("ERROR: File {} already exists in directory, skipping...".format(dl_name))
        return False

    print("Downloading {}...".format(dl_name))
    dl_url_with_api = "{}?api_key={}".format(dl_url, g_api_key)
    try:
        urllib.request.urlretrieve(dl_url_with_api, dl_name, show_progress)
        
        # Add downloaded video to done file
        with open(g_done_file, "a", encoding="utf-8") as done_file:
            done_file.write("\"{}\",\"{}\"\n".format(dl_name, dl_url))

        inc_and_check_rq_rate()
        inc_and_check_dl_rate()
    except Exception as e:
        print(e)
        print("ERROR: Exception during video {} download!\nURL: {}".format(dl_name, dl_url_with_api))
        return False

    return True

################################################################################
# Desc
#   Saves dl_dict and done_dict to progress files
# Params
#   dl_dict         dict of videos to download
#   done_dict       dict of videos already downloaded
# Returns
#   None
################################################################################
def save_progress(dl_dict, done_dict):
    # Log videos that need to be downloaded
    try:
        with open(g_dl_file, "w", encoding="utf-8") as dl_file:
            for dl_name, dl_url in dl_dict.items():
                dl_file.write("\"{}\",\"{}\"\n".format(dl_name, dl_url))
    except Exception as e:
        print(e)
        print("WARN: Exception when writing [{}, {}] to {}! Skipping...".format(dl_name, dl_url, g_dl_file))

    # Log videos that have already been downloaded
    try:
        with open(g_done_file, "w", encoding="utf-8") as done_file:
            for done_name, done_url in done_dict.items():
                done_file.write("\"{}\",\"{}\"\n".format(done_name, done_url)) 
    except Exception as e:
        print(e)
        print("WARN: Exception when writing [{}, {}] to {}! Skipping...".format(done_name, done_url, g_done_file))

################################################################################
# Desc
#   Loads dl_dict and done_dict from progress files
# Params
#   None
# Returns
#   dl_dict         dict of videos to download
#   done_dict       dict of videos already downloaded
################################################################################
def load_progress():
    dl_dict = OrderedDict()
    done_dict = {}

    # Load files to download
    try:
        with open(g_dl_file, "r", encoding="utf-8") as dl_file:
            print("Loading files to download from {}...".format(g_dl_file))
            dl_reader = csv.reader(dl_file)
            for row in dl_reader:
                dl_dict[row[0]] = row[1]     
    except FileNotFoundError:
        print("Progress file {} not found. Creating...".format(g_dl_file))
        with open(g_dl_file, "w", encoding="utf-8") as dl_file:
            pass
    except Exception as e:
        print(e)
        print("ERROR: Exception when loading from {}".format(g_dl_file))
        return None, None

    # Load files that are finished
    try:
        with open(g_done_file, "r", encoding="utf-8") as done_file:
            print("Loading completed files from {}...".format(g_done_file))
            done_reader = csv.reader(done_file)
            for row in done_reader:
                done_dict[row[0]] = row[1] 
    except FileNotFoundError:
        print("Progress file {} not found. Creating...".format(g_done_file))
        with open(g_done_file, "w", encoding="utf-8") as done_file:
            pass
    except Exception as e:
        print(e)
        print("ERROR: Exception when loading from {}".format(g_done_file))
        return None, None

    return dl_dict, done_dict

################################################################################
# Desc
#   Increments and checks download rate, returns when rate limit is not exceeded
# Params
#   None
# Returns
#   None
################################################################################
def inc_and_check_dl_rate():
    global g_start_time
    global g_dl_count
    global g_max_dl_rate
    g_dl_count += 1
    curr_time = time.time()
    curr_rate = g_dl_count / (curr_time - g_start_time)
    print("Videos downloaded {}".format(g_dl_count))

    # Sleep while curr rate is over the max rate
    while curr_rate > g_max_dl_rate:
        print("Videos downloaded {}, Current dl rate {}, Max dl rate {}".format(g_dl_count, curr_rate, g_max_dl_rate))
        # Sleep 1 minute
        sleep_bar(60)
        # Calculate new rate
        curr_time = time.time()
        curr_rate = g_dl_count / (curr_time - g_start_time)

################################################################################
# Desc
#   Increments and checks request rate, returns when rate limit is not exceeded
# Params
#   None
# Returns
#   None
################################################################################
def inc_and_check_rq_rate():
    global g_start_time
    global g_rq_count
    global g_max_rq_rate
    g_rq_count += 1
    curr_time = time.time()
    curr_rate = g_rq_count / (curr_time - g_start_time)
    #print("Requests made {}".format(g_rq_count))

    # Sleep while curr rate is over the max rate
    while curr_rate > g_max_rq_rate:
        print("Requests made {}, Current rq rate {}, Max rq rate {}".format(g_rq_count, curr_rate, g_max_rq_rate))
        # Sleep 10 seconds
        sleep_bar(10)
        # Calculate new rate
        curr_time = time.time()
        curr_rate = g_rq_count / (curr_time - g_start_time)      

################################################################################
# Desc
#   Shows progress during download, compatible with urllib.request.urlretrieve
# Params
#   block_num       int current block number of download
#   block_size      int current block size of download
#   total_size      int total size of download
# Returns
#   None
################################################################################
def show_progress(block_num, block_size, total_size):
    global g_pbar
    if g_pbar is None:
        g_pbar = ProgressBar(total_size)
        g_pbar.start()

    downloaded = block_num * block_size
    if downloaded < total_size:
        g_pbar.update(downloaded)
    else:
        g_pbar.finish()
        g_pbar = None

################################################################################
# Desc
#   Simple progress bar class, to be used with show_progress function
################################################################################
class ProgressBar:
    def __init__(self, dl_size):
        self.maxval = 50
        self.currval = 0
        self.dl_size = dl_size

        print("|0%", end="")
        for k in range(round(self.maxval/2.0)-7):
            print(" ", end="")
        print("Progress", end="")
        for k in range(round(self.maxval/2.0)-7):
            print(" ", end="")
        print("100%|")

    def start(self):
        print("|", end="")

    def update(self, updateval):
        while self.currval < round(self.maxval*(updateval/float(self.dl_size))):
            print(".", end="", flush=True)
            self.currval += 1

    def finish(self):
        print('|')

################################################################################
# Desc
#   Prints a sleep bar (in seconds)
# Params
#   sleep_time      int time to sleep in seconds
# Returns
#   None
################################################################################
def sleep_bar(sleep_time):
    maxval = 50
    print("|0%", end="")
    for k in range(int(maxval/2)-6):
        print(" ", end="")
    print("Sleep ", end="")
    for k in range(int(maxval/2)-6):
        print(" ", end="")
    print("100%|\n|", end="")
    
    sleep_cnt = 0
    print_cnt = 0
    while sleep_cnt < sleep_time:
        time.sleep(1)
        sleep_cnt += 1
        print_amount = int(maxval*(sleep_cnt/sleep_time)) - print_cnt
        for k in range(print_amount):
            print(".", end="", flush=True)
            print_cnt += 1
    print('|')

################################################################################
# Desc
#   Prints usage
# Params
#   None
# Returns
#   None
################################################################################
def print_usage():
    print("Usage: dl_gb.py [OPTION]...                                      ")
    print("  Note: both query and download modes will be enabled by default ")
    print("  -q, --query                                                    ")
    print("      Query mode only: query for videos and log them in dl.csv   ")
    print("  -d, --download                                                 ")
    print("      Download mode only: download all videos in dl.csv          ")

# Strip off script name in arg list
if __name__ == "__main__":
    main(sys.argv[1:])