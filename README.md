# Requirements
- Giantbomb premium account (API key)
- Python 3

# How To Run
1. Create a file in the same folder called `api_key.txt`, and paste your Giantbomb account API key there
    1. Find it here: https://www.giantbomb.com/api/
2. **(Optional)** Modify `g_skip_titles` variable in `dl_gb.py` to skip certain shows. 
    1. Ex: `g_skip_titles = ["Giant Bombcast", "The Giant Beastcast"]`
3. Run the script!

# Usage
**dl_gb.py [OPTION]...**

Note: both query and download modes will be enabled by default

* -h
    * Prints help message
* -q, \-\-query
    * Query mode only: query for videos and log them in dl.csv
* -d, \-\-download
    * Download mode only: download all videos in dl.csv

# Generated Files
* `dl.csv`: logs all videos found in Query mode, to be downloaded later
* `done.csv`: logs all videos successfully downloaded during Download mode
* `error.csv`: logs all videos that failed to download during Download mode