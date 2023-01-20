#!/usr/bin/python3

import re, sys, os
import getopt
import time
import csv

import urllib.request
import xml.etree.ElementTree as ET

from collections import OrderedDict

# Scrapes and downloads premium videos from Giantbomb.

# Requires a premium account API key placed in api_key.txt in the same directory
# as this script. There is an undocumented limit of 100 videos downloaded per
# day and a documented max request rate of 200 requests per hour.

################################################################################
# Globals 
################################################################################
g_gb_url = "https://www.giantbomb.com"
g_api_key = "" # To be initialized in main
g_progress_file = "progress.csv"
g_pbar = None
g_start_time = time.time()

g_dl_count = 0                  # Download count
g_rq_count = 0                  # Request count
g_max_dl_rate = 100/(24*60*60)  # Max 100 videos per day (in videos/second)
g_max_rq_rate = 200/(60*60)     # Max 200 requests per hour (in requests/second)

# Regex patterns
g_premium_page_pattern = re.compile("\s+<a href=\"(?P<url>/(?:shows|videos)/[/\-\w\d]+/(?P<guid>\d{2,6}\-\d{2,6})).*")
g_dl_url_pattern = None
g_publish_date_pattern = re.compile("([\d-]+) [\d:]+")
g_video_dl_name_pattern = re.compile(".*/(.*)\.mp4\s*")

################################################################################
# Main
################################################################################
def main(argv):
    # Init api key
    try:
        global g_api_key
        g_api_key = open("api_key.txt", "r").read()
        if g_api_key == "":
            print("ERROR: Invalid API key from api_key.txt! Please paste your valid API key in there. Exiting...")
            return 1
    except:
        print("ERROR: Missing api_key.txt! Please create this file with only you API key in it. Exiting...")
        return 1

    # Init regex pattern
    global g_dl_url_pattern
    g_dl_url_pattern = re.compile("\s+<a href=\"(.*mp4\?api_key={})\"".format(g_api_key))

    # Helper function to get first item in an ordered dictionary
    def get_first_pair(d):
        return next(iter(d))

    # Scrape guids if no progress file found
    dl_dict = OrderedDict()
    progress_file_path = "./{}".format(g_progress_file)
    if not os.path.isfile(progress_file_path):
        # Run through all premium pages (currently at 55)
        print("Scraping guids from premium pages...")
        page_no = 55
        while page_no > 0:
            url_list, guid_list = get_url_list_from_page(page_no)
            
            # For each premium video, get download url
            for guid in guid_list:
                dl_name, dl_url = get_dl_url_from_guid(guid)
                if dl_name and dl_url:
                    dl_dict[dl_name] = dl_url

            # Write out dl_dict to progress file
            save_progress(dl_dict)
            page_no -= 1
    else:
        print("{} found, skip scraping guids step".format(g_progress_file))

        # Load progress.csv into dl_dict
        # TODO

    
    # For each download url...
    while len(dl_dict) > 0:
        # Download video (FIFO) and cut it from list
        dl_name, dl_url = get_first_pair(dl_dict)
        if download_video(dl_name, dl_url):
            del dl_dict[dl_name]
        else:
            # If download fails, put pair at the bottom to try again later
            dl_dict.move_to_end(dl_name)

        # Once video is done downloading, update the progress file
        save_progress(dl_dict)

    # Delete empty progress file
    print("Done downloading all videos! Deleting {}...".format(g_progress_file))
    os.remove(progress_file_path)

    return 0

################################################################################
# Gets video urls and guids from premium page
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
# Gets download urls and forms download name from videos API call
# All-in-one step vs calling get_url_list_from_page and get_dl_url_from_guid
# offset changes which set of videos are queried by API
################################################################################
def get_dl_urls_from_api(offset):
    filter = "premium:true"
    limit = 100 # Hard limit by API
    sort = "id:asc"
    field_list = ["name", "publish_date", "hd_url", "high_url", "low_url"]
    
    # Query list of premium videos from API
    xml_url = "https://www.giantbomb.com/api/videos/?api_key={}&offset={}"
    "&filter={}&limit={}&sort={}&field_list={}".format(g_api_key, offset, filter, limit, sort, ','.join(field_list))

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
# Gets download url from guid, and what to name it
################################################################################
def get_dl_url_from_guid(guid):
    field_list = ["name", "publish_date", "hd_url", "high_url", "low_url"]

    # Query xml from website
    xml_url = "https://www.giantbomb.com/api/video/{}/?api_key={}"
    "&field_list={}".format(guid, g_api_key, ','.join(field_list))
    
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
# Downloads a video from dl url, returns true on success, else false
################################################################################
def download_video(dl_name, dl_url):
    print("Downloading {}...".format(dl_name))
    try:
        urllib.request.urlretrieve(dl_url, dl_name, show_progress)
        inc_and_check_rq_rate()
        inc_and_check_dl_rate()
        return True
    except Exception as e:
        print(e)
        print("ERROR: Exception during video {} download!".format(dl_name))
        return False

################################################################################
# Saves dict of dl_names and dl_urls to progress file
################################################################################
def save_progress(dl_dict):
    progress_file = open(g_progress_file, "w", encoding="utf-8")
    for dl_name, dl_url in dl_dict.items():
        try:
            progress_file.write("\"{}\",\"{}\"\n".format(dl_name, dl_url))
        except Exception as e:
            print(e)
            print("ERROR: Exception when writing [{}, {}] to progress.csv! Skipping...".format(dl_name, dl_url))
    progress_file.close()

################################################################################
# Loads dict of dl_names and dl_urls from progress file
################################################################################
def load_progress():
    dl_dict = {}
    try:
        progress_file = open(g_progress_file, "r", encoding="utf-8")
        progress_reader = csv.reader(progress_file)
        for row in progress_reader:
            dl_dict[row[0]] = row[1]
    except Exception as e:
        print(e)
        print("ERROR: Exception during loading from progress.csv!")
        return {}

    return dl_dict

################################################################################
# Increments and checks download rate, returns when rate limit is not exceeded
################################################################################
def inc_and_check_dl_rate():
    global g_start_time
    global g_dl_count
    global g_max_dl_rate
    g_dl_count += 1
    curr_time = time.time()
    curr_rate = g_dl_count / (curr_time - g_start_time)
    #print("Videos downloaded {}".format(g_dl_count))

    # Sleep while curr rate is over the max rate
    while curr_rate > g_max_dl_rate:
        print("Videos downloaded {}, Current dl rate {}, Max dl rate {}".format(g_dl_count, curr_rate, g_max_dl_rate))
        # Sleep 1 minute
        sleep_bar(60)
        # Calculate new rate
        curr_time = time.time()
        curr_rate = g_dl_count / (curr_time - g_start_time)

################################################################################
# Increments and checks request rate, returns when rate limit is not exceeded
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
# Shows progress during download
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
# Prints a progress bar
################################################################################
class ProgressBar:
    def __init__(self, dl_size):
        self.maxval = 50
        self.currval = 0
        self.dl_size = dl_size

        print("|0%", end="")
        for k in range(int(self.maxval/2)-5):
            print(" ", end="")
        print("Progress ", end="")
        for k in range(int(self.maxval/2)-5):
            print(" ", end="")
        print("100%|")

    def start(self):
        print("|", end="")

    def update(self, updateval):
        while self.currval < int(self.maxval*(updateval/self.dl_size)):
            print(".", end="", flush=True)
            self.currval += 1

    def finish(self):
        print('|')

################################################################################
# Prints a sleep bar (in seconds)
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
# Prints usage
################################################################################
def print_usage():
    print("Usage: scrape_gb.py [OPTION]...")
    # print("  -d, --clip_dir")
    # print("      directory to clip files from")
    # print("  -l, --language")
    # print("      audio language track to clip, defaults to \"jpn\"")
    # print("  -s, --clip_start")
    # print("      start time (in minutes) where to clip in each file, if not specified a random position is chosen")
    # print("  -t, --clip_length")
    # print("      time to clip out (in minutes), defaults to 5 minutes")


# Strip off script name in arg list
if __name__ == "__main__":
    main(sys.argv[1:])