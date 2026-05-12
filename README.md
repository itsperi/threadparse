# Threadparser

## Description
Threadparser is a collection of Python scripts that leverages the `ast` library in order to parse Python files or directories/repositories containing Python files to detect potentially unsafe multithreaded code.

## How To Run
There are 2 main files that drive the analysis pipeline:
1. `parse.py`, which takes files or directories and outputs json results (verbose output should be redirected to another file), as well as a text summary of files that were flagged for unsafe thread behavior
2. `stats.py`, which takes json results and provides a summary, mainly for use for large datasets (this should be redirected to another file)

...as well as 2 utilities in the event you need to populate a directory with repos from Github:
1. `puller.py`, which uses a user provided Github API token in `.env` to request repos that match a provided query, and produces a file containing a list of resultant Python repos from the query. 
2. `clone.sh`, which takes a file containing Github URLs, and locally clones them into a provided directory

Given a directory `<files>` containing Python files that you would like to check for unsafe thread behavior for, run:

```python parse.py -s -o results.json files ```

This will output `results.json`, which is used as input in the stats.py script as such:

```python stats.py results.json -o parser_results > summary.txt ```

## Preparation
If you do not already have a target directory full of Python repos/files you wish to analyze, then you must run:

```python puller.py -q "your query here" urls.txt``` with a query that should return repositories that include multithreaded Python code on Github.

Then, run ```./clone.sh urls.txt <files>``` to locally clone into a repository to use in the above pipeline

## Usage
> ### `parse.py`
#### ``` python parse.py [-h | --help] | [-v | --verbose] [-s | --silent] [-o | --output <filename>] <files>```

      -h | --help                Outputs this usage information; also outputs if no arguments provided
      
      -v | --verbose             Enable verbose output (all detected shared accesses, with line numbers);
                                 this should be redirected to another file, especially if parsing large datasets
                                 
      -s | --silent              Enable silent output
      
      -o | --output <filename>   Output results to JSON file

      <files>                    Sequentially listed files or directories that will be analyzed

> ### `stats.py`
#### ``` python stats.py [-h | --help] | <input.json> [-o | --out-dir <dir>]```

      -h | --help                Outputs this usage information; also outputs if no arguments are provided
      
      <input.json>               Input JSON from `parse.py`
      
      -o | --out-dir <dir>       Designate a directory to receive CSV output files; default is current directory

> ### `puller.py`
#### ``` python puller.py [-q | --query] <dest>```
      -q | --query <search>      Designate a specific search query to the API request
                                 (Defaults to "language:python threading in:code")

      <dest>                     Designate the file to place URL results into

> ### `clone.sh`
#### ``` ./clone.sh <url_file> [target_dir]```
      <url_file>                 File that contains Github URLs on each line

      target_dir                 Directory to store all cloned repositories
                                 (Defaults to /clones)

# Disclaimer
The analysis pipeline makes no assumptions about the Python code it processes, only that if it uses threading, and threads share state, then it will look for unprotected shared access/mutations. 

If any code is flagged, it is up to the user to determine whether the threaded code results in incorrect/unintended behavior