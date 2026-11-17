"""The source_build tests load build*.py by file path (spec_from_file_location).

Without this, a plain `pytest` run writes __pycache__/build*.pyc into the source folder
that is zipped for the Windows build PC.
"""
import sys

sys.dont_write_bytecode = True
