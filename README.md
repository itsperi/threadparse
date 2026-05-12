# Threadparser

## Description
Threadparser is a collection of Python scripts that leverages the `ast` library in order to parse Python files or directories/repositories containing Python files to detect potentially unsafe multithreaded code.

## How to run
There are 2 main files that drive the parsing pipeline:
1. `parse.py`, which takes files or directories and outputs json results (verbose output should be redirected to another file)
2. `stats.py`, which takes json results and provides a summary, mainly for use for large datasets (this should be redirected to another file)

### `parse.py` Usage
``` python parse.py [-h | --help] | [-v | --verbose] [-s | --silent] [[-o | --output] <filename>] <file_or_dir_paths>```

      -h | --help                Outputs this usage information; also outputs if no arguments provided
      
      -v | --verbose             Enable verbose output (all detected shared accesses, with line numbers);
                                 this should be redirected to another file, especially if parsing large datasets
                                 
      -s | --silent              Enable silent output
      
      -o | --output <filename>   Output results to JSON file

### `stats.py` Usage
``` python stats.py [-h | --help] | <input.json> [--out-dir <dir>]```

      -h | --help                Outputs this usage information; also outputs if no arguments are provided
      
      <input.json>               Input JSON from `parse.py`
      
      --out-dir <dir>            Designate a directory to receive CSV output files

## Utilities
Included are utility files (that aren't comprehensive, but can be changed to fit your purposes).

`puller.py` uses a Github API token provided by the user in the root directory's `.env` to call for urls of repos that match the query "language:python threading in:code", and outputs these to "github_results.txt". These can be changed to suit your needs.

`clone.sh <file> <target>` takes an input file with Github repos on each line, and a target directory where the repos will be cloned locally.

## Example usage
With a directory named `files` in the root directory, run  `python parse.py -s -o results.json files` then run `python stats.py results.json --out-dir summary > summary.txt`.

If you need to examine files more closely, run `parse.py` with the `-v` flag and redirect to your file of choice.
