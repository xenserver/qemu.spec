#!/usr/bin/env python3

# Copyright (c) 2026. Citrix Systems, Inc. All Rights Reserved. Confidential & Proprietary

import optparse
import os
import re
import subprocess
import sys
from functools import total_ordering

def call(args):
    try:
        return subprocess.check_output(args, stderr=subprocess.STDOUT, text=True)
    except OSError as e:
        sys.stderr.write("%s: `%s': %s\n" % (sys.argv[0], args[0], e.strerror))
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(e.output)
        sys.stderr.write("%s: `%s': returned %d\n" % (sys.argv[0], args[0], e.returncode))
        sys.exit(2)

@total_ordering
class version(object):
    version_regex = re.compile("^(\\d+)\\.(\\d+)(\\.(\\d+))?(-rc(\\d+))?$")

    class bad_version(Exception):
        pass

    def __init__(self, ver_str):
        self.str = ver_str

        match = self.version_regex.match(self.str)
        if not match:
            raise version.bad_version()

        self.major = int(match.group(1))
        self.minor = int(match.group(2))
        if match.group(4):
            self.micro = int(match.group(4))
        else:
            self.micro = 0
        if match.group(6):
            self.rc = int(match.group(6))
        else:
            self.rc = None

    def __lt__(self, v):
        if self.major > v.major:
            return False
        if self.major < v.major:
            return True
        if self.minor > v.minor:
            return False
        if self.minor < v.minor:
            return True
        if self.micro > v.micro:
            return False
        if self.micro < v.micro:
            return True

        # major.minor.micro match -- check rc
        if self.rc and not v.rc:
            return True
        if not self.rc and v.rc:
            return False
        if not self.rc and not v.rc:
            return False
        return self.rc < v.rc

    def __eq__(self, v):
        return self.major == v.major and self.minor == v.minor and self.micro == v.micro and self.rc == v.rc

    def __str__(self):
        return self.str

    @staticmethod
    def test():
        test_data = [ ( "3.9", "3.9", False ),
                      ( "3.9", "3.10", True ),
                      ( "3.11", "3.10", False ),
                      ( "3.10.25", "3.10.25", False ),
                      ( "3.10.25", "3.10.26", True ),
                      ( "3.10.25", "3.10.24", False ),
                      ( "3.10-rc3", "3.10-rc3", False ),
                      ( "3.10-rc3", "3.10-rc4", True ),
                      ( "3.10-rc3", "3.10-rc2", False ),
                      ( "3.10-rc3", "3.10", True ),
                      ( "3.10", "3.10-rc3", False ),
                      ( "3.9", "3.10-rc3", True ),
                      ( "3.10-rc2", "3.9", False ),
                      ( "3.10-rc2", "3.11-rc2", True ),
                      ( "3.11-rc2", "3.10-rc2", False ),
                      ( "3.10", "4.9", True ),
                      ]


        try:
            v = version("bad")
            raise Exception('Expected exception')
        except version.bad_version as e:
            pass

        for d in test_data:
            a = version(d[0])
            b = version(d[1])
            expected = d[2]

            result = a < b
            if result != expected:
                raise Exception(f"{a}, {b} != {expected} (was {result})")

class patch(object):
    def __init__(self, commit, patch_file, ver):
        self.commit = commit
        self.patch_file = patch_file
        self.ver = ver

    def write(self, f):
        if self.ver:
            v = " (%s)" % (self.ver)
        else:
            v = ""
        f.write("# %s%s\n%s\n" % (self.commit, v, self.patch_file))

version.test()

parser = optparse.OptionParser(
    usage = "usage: %prog [option...] <commit ID>...",
    description="Import git commits into a patch queue.")

parser.add_option("-r", "--repo", dest="repo", default=".",
                  help="path to source git repository", metavar="REPO")
parser.add_option("-l", "--list", dest="list",
                  help="file containing a list of commit IDs", metavar="FILE")
parser.add_option("-c", "--chrono", dest="chrono", action='store_true',
                  help="Force chronological ordering of commits")
(options, args) = parser.parse_args()

if options.chrono and (not options.list):
    parser.error("options -c must be used in conjunction with -l.")

commits = []
commit_info = []

if options.list:
    list_file = open(options.list, "r")
    for l in list_file.readlines():
        commit = l.strip()

        if commit != "":
            if options.chrono:
                commit_info.append(call(["git", "show", "-s", commit, "--date=iso", "--pretty", "--format=\"%ad %H\""]))
            else:
                commits.append(commit)

    if options.chrono:
        for x in sorted(commit_info):
            commits.append(x.split()[-1].replace("\"", ""))

commits.extend(args)

if len(commits) == 0:
    sys.stderr.write("%s: no commits specified\n" % (sys.argv[0]))
    sys.exit(1)

repo = options.repo + "/.git"

# planex creates a 'planex/v4.19.19' branch for example
current_branch = call(["git", "--git-dir", repo, "rev-parse", "--abbrev-ref", "HEAD"]).strip().replace("guilt/", "", 1)
patch_dir = os.path.realpath(".git/patches/" + current_branch)
patch_base = patch_dir + "/../"
patch_repo = os.path.realpath(patch_base + "/.git")

if not os.path.isdir(repo):
    sys.stderr.write("%s: `%s' is not a git repository\n" % (sys.argv[0], options.repo))
    sys.exit(1)

if not os.path.isdir(patch_dir):
    sys.stderr.write("%s: no patch queue present at %s\n" % (sys.argv[0], patch_dir))
    sys.exit(1)

patches = []

n = 1
for commit in commits:
    print(commit)

    out = call(["git", "--git-dir", repo,
                "format-patch", "--start-number", str(n), "--numbered",
                "--output-directory", patch_dir,
                "%s^!" % commit])
    patch_file = os.path.basename(out.strip())

    tags = []
    earliest_ver = None

    out = call(["git",  "--git-dir", repo,
                "tag", "--contains", commit])
    for tag in out.split("\n"):
        if tag != "" and tag[0] == "v":
            try:
                ver = version(tag[1:])
            except:
                continue
            if not earliest_ver or ver < earliest_ver:
                earliest_ver = ver

    p = patch(commit, patch_file, earliest_ver)
    patches.append(p)

    n += 1

#
# Call git add on all the patch files.
#

for p in patches:
    call(["git", "--git-dir", patch_repo, "-C", patch_base,
          "add", "patches/" + p.patch_file])

#
# Add patch files to series file in approximately the right places.
#

series = open(patch_dir + "/series", "r")
series_lines = series.readlines()
series.close()

series = open(patch_dir + "/series.new", "w+")

state = 0
for line in series_lines:
    l = line.strip()
    if l == "# Backports from upstream":
        state = 1
    if state == 1 and l == "":
        for p in patches:
            p.write(series)
        state = 2
    series.write(line)
if state < 2:
    for p in patches:
        p.write(series)

series.close()

os.rename(patch_dir + "/series.new", patch_dir + "/series")
call(["git", "--git-dir", patch_repo, "-C", patch_base,
      "add", "patches/series"])
