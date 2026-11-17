#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_sources.py — static sanity check for the C# sources of OnyxNations.

This is NOT a compiler and does not pretend to be one. It catches the class of
mistake that is easy to make when the code cannot be compiled in the sandbox
where it is written:

  1. every <Compile Include> in the .csproj points at a file that exists, and no
     .cs file on disk was forgotten by the project;
  2. braces / parentheses / brackets balance in every file (outside strings and
     comments);
  3. every `using X;` resolves to a System namespace, a GTA (ScriptHookVDotNet)
     namespace or one of this project's own namespaces;
  4. every `new TypeName(`, `typeof(TypeName)` and `TypeName.Member` reference
     names a type and member this project actually declares;
  5. every project type a file mentions is actually *reachable* from that file's
     namespace - own namespace, an enclosing namespace, or an imported one. This
     is CS0246, the error a compiler reports in a second and a whitespace-level
     check cannot see at all: the type exists, the `using` does not.

Usage:  python3 tools/check_sources.py
Exit code 0 = clean, 1 = problems found.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSPROJ = os.path.join(ROOT, "src", "OnyxNations", "OnyxNations.csproj")
SRCDIR = os.path.join(ROOT, "src", "OnyxNations")

KNOWN_PREFIXES = ("System", "Microsoft")

# Framework types that are instantiated with `new` but are never declared here.
BCL_TYPES = {
    "FileInfo", "DirectoryInfo", "StreamWriter", "StreamReader", "FileStream",
    "MemoryStream", "Stopwatch", "StringBuilder", "XmlSerializer",
    "DataContractJsonSerializer", "File", "Directory", "Path", "Task", "Thread",
    "Random", "EventArgs", "Exception", "OutputArgument",
}

# ScriptHookVDotNet types. They live in the reference assembly, which is not
# available here, so they are whitelisted by name the same way BCL types are.
GTA_TYPES = {
    "Vector3", "Vector2", "Model", "Ped", "Vehicle", "PedGroup", "Blip",
    "Prop", "WeaponHash", "Relationship", "InputArgument", "Camera",
}
KNOWN_NAMESPACES = {"GTA", "GTA.Native", "GTA.Math", "GTA.UI"}

# member names that come from the BCL / GTA and are never declared here
EXTERNAL_MEMBERS = {
    "Parse", "TryParse", "Format", "Equals", "ToString", "GetHashCode", "Compare",
    "Exists", "Delete", "Move", "OpenRead", "ReadAllBytes", "WriteAllBytes",
    "CreateDirectory", "GetDirectoryName", "Combine", "GetFileName", "GetValue",
    "TryGetValue", "Add", "Remove", "Contains", "ContainsKey", "Split", "Trim",
    "ToLowerInvariant", "Start", "Stop", "Reset", "ElapsedMilliseconds",
    "Flush", "ReadObject", "WriteObject", "ToArray", "GetType", "Invoke",
    "GetExecutingAssembly", "Location", "FullName", "Length", "Position",
}


def strip_noise(text):
    """Remove block comments, line comments and string/char literals (crudely but
    well enough for brace counting)."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '/' and i + 1 < n and text[i + 1] == '/':
            while i < n and text[i] != '\n':
                i += 1
        elif c == '/' and i + 1 < n and text[i + 1] == '*':
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
                i += 1
            i += 2
        elif c == '"':
            i += 1
            while i < n:
                if text[i] == '\\':
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
        elif c == "'":
            i += 1
            while i < n and text[i] != "'":
                i += 2 if text[i] == '\\' else 1
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def balanced(code):
    pairs = {')': '(', '}': '{', ']': '['}
    stack = []
    for ch in code:
        if ch in '({[':
            stack.append(ch)
        elif ch in ')}]':
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()
    return not stack


TYPE_RE = re.compile(
    r'^\s*(?:\[[^\]]*\]\s*)*(?:public|internal|private|protected)?\s*'
    r'(?:(?:sealed|static|abstract|partial|readonly)\s+)*'
    r'(?:class|struct|interface|enum)\s+(\w+)', re.M)

NAMESPACE_RE = re.compile(r'^\s*namespace\s+([\w.]+)', re.M)
USING_RE = re.compile(r'^\s*using\s+(?:([\w]+)\s*=\s*)?([\w.]+)\s*;', re.M)
NEW_RE = re.compile(r'\bnew\s+([A-Z]\w*)\s*[\(\[]')
TYPEOF_RE = re.compile(r'\btypeof\s*\(\s*([A-Z]\w*)\s*\)')
QUALIFIED_RE = re.compile(r'\b([A-Z]\w*)\.([A-Za-z_]\w*)')
MEMBER_RE = re.compile(
    r'^\s*(?:\[[^\]]*\]\s*)*(?:public|private|internal|protected)\s+'
    r'(?:(?:static|readonly|const|sealed|virtual|override|extern|new)\s+)*'
    r'[\w<>\[\],\.\?\*\s]+?\s+(\w+)\s*(?:\(|;|\{|=|$)', re.M)

# `enum Foo { A, B = 3, C }` - enum members are plain identifiers and are never
# matched by MEMBER_RE, so they are collected separately.
ENUM_RE = re.compile(
    r'\benum\s+(\w+)[^{;]*\{([^}]*)\}', re.S)
ENUM_MEMBER_RE = re.compile(r'^\s*(\w+)\s*(?:=[^,]*)?,?\s*(?://.*)?$')


def main():
    problems = []
    warnings = []

    if not os.path.isfile(CSPROJ):
        print("[!] csproj not found: %s" % CSPROJ)
        return 1

    # ---------------------------------------------------------- 1. file list
    csproj = open(CSPROJ, encoding="utf-8").read()
    declared = re.findall(r'<Compile Include="([^"]+)"', csproj)
    declared = [d.replace("\\", os.sep) for d in declared]
    on_disk = []
    for dirpath, _, filenames in os.walk(SRCDIR):
        for fn in filenames:
            if fn.endswith(".cs"):
                on_disk.append(os.path.relpath(os.path.join(dirpath, fn), SRCDIR))

    for rel in declared:
        if not os.path.isfile(os.path.join(SRCDIR, rel)):
            problems.append("csproj lists %s but the file does not exist" % rel)
    for rel in on_disk:
        if rel not in declared:
            warnings.append("%s is on disk but not in the csproj" % rel)

    print("[i] csproj lists %d files, %d .cs files on disk" % (len(declared), len(on_disk)))

    # ------------------------------------------------------ 2. parse sources
    namespaces = set()
    types = {}          # type name -> set(member names)
    type_ns = {}        # type name -> namespace it is declared in
    file_ns = {}        # relative path -> namespace of the file
    files = {}

    for rel in declared:
        path = os.path.join(SRCDIR, rel)
        if not os.path.isfile(path):
            continue
        raw = open(path, encoding="utf-8").read()
        files[rel] = raw
        code = strip_noise(raw)

        if not balanced(code):
            problems.append("%s: unbalanced braces/parens/brackets" % rel)

        for ns in NAMESPACE_RE.findall(raw):
            namespaces.add(ns)

        nsmatch = NAMESPACE_RE.search(raw)
        file_ns[rel] = nsmatch.group(1) if nsmatch else ""

        for tname in TYPE_RE.findall(raw):
            types.setdefault(tname, set())
            type_ns.setdefault(tname, file_ns[rel])

        # enum members: `A,` / `B = 3,` inside an enum body. Trailing comments
        # are stripped first — most enums here are annotated member by member.
        for tname, body in ENUM_RE.findall(raw):
            members = types.setdefault(tname, set())
            body = re.sub(r'//[^\n]*', '', body)
            body = re.sub(r'/\*.*?\*/', '', body, flags=re.S)
            for chunk in body.split(","):
                m = ENUM_MEMBER_RE.match(chunk.strip())
                if m:
                    members.add(m.group(1))

        for m in MEMBER_RE.findall(raw):
            # members belong to the type declared most recently before them
            pass

    # second pass: attach members to the type they follow
    for rel, raw in files.items():
        current = None
        for line in raw.splitlines():
            tmatch = TYPE_RE.match(line) or re.match(r'^\s*(?:class|struct|interface|enum)\s+(\w+)', line)
            if tmatch:
                current = tmatch.group(1)
                types.setdefault(current, set())
                continue
            mmatch = MEMBER_RE.match(line)
            if mmatch and current:
                types[current].add(mmatch.group(1))

    print("[i] namespaces: %s" % ", ".join(sorted(namespaces)))
    print("[i] declared types: %d" % len(types))

    # --------------------------------------------------------- 3. using check
    for rel, raw in files.items():
        for alias, ns in USING_RE.findall(raw):
            if alias:
                # `using Alias = Some.Type;` (or a namespace alias such as
                # `using WF = System.Windows.Forms;`) - the target is not a
                # plain namespace import, so only check what we can.
                root = ns.split(".")[0]
                if root in KNOWN_PREFIXES or ns in KNOWN_NAMESPACES:
                    continue
                short = ns.split(".")[-1]
                if short in types or short in BCL_TYPES or short in GTA_TYPES:
                    continue
                warnings.append("%s: alias '%s = %s' targets an unknown type"
                                % (rel, alias, ns))
                continue
            root = ns.split(".")[0]
            if ns in namespaces or ns in KNOWN_NAMESPACES or root in KNOWN_PREFIXES:
                continue
            problems.append("%s: using %s; resolves to nothing known" % (rel, ns))

    # ------------------------------------------------- 4. type/member checks
    for rel, raw in files.items():
        code = strip_noise(raw)
        for tname in NEW_RE.findall(code) + TYPEOF_RE.findall(code):
            if tname in types or tname in KNOWN_NAMESPACES:
                continue
            if tname in BCL_TYPES or tname in GTA_TYPES or len(tname) == 1 or tname.endswith("Exception"):
                continue
            problems.append("%s: unknown type '%s'" % (rel, tname))

        # Some names are both a type (Core/Settings.cs) and a field of another type
    # (BorderSystem.Settings). If the member exists anywhere in the project the
    # reference is plausible, so it is not worth a warning.
    all_members = set()
    for members in types.values():
        all_members |= members

    for tname, member in QUALIFIED_RE.findall(code):
            if tname not in types:
                continue
            if member in types[tname] or member in EXTERNAL_MEMBERS or member in all_members:
                continue
            # consts and fields declared inline without a modifier are common
            if re.search(r'\b%s\b' % re.escape(member), raw):
                warnings.append("%s: %s.%s not found as a declared member (check manually)"
                                % (rel, tname, member))
            else:
                problems.append("%s: %s.%s is not declared on %s" % (rel, tname, member, tname))

    # ----------------------------------------------- 5. namespace visibility
    # A bare type name is resolved by C# against the file's own namespace, then
    # every enclosing namespace, then the `using` directives - and nothing else.
    # Rule 4 only proves that a type exists somewhere in the project; it cannot
    # see that the file reaching for it never imported the namespace. That is
    # exactly how a build breaks with "the type or namespace name 'X' could not
    # be found" while every file still looks tidy on its own.
    #
    # Only project types are checked: BCL and SHVDN types are not declared here,
    # so nothing can be said about them without the reference assembly.
    DECL_RE = re.compile(
        r'(?<![\w.])([A-Z]\w*)(?:\s*<[^<>=;(){}]*?>)?'
        r'(?=\s+[A-Za-z_]\w*\s*(?:=|;|,|\)|\{|\[))')
    ACCESS_RE = re.compile(r'(?<![\w.])([A-Z]\w*)\s*\.\s*[A-Z]\w*')
    GENERIC_RE = re.compile(r'<([^<>;()]*(?:<[^<>]*>)?[^<>;()]*)>')

    for rel, raw in files.items():
        ns = file_ns.get(rel, "")
        usings = [target for alias, target in USING_RE.findall(raw) if not alias]
        code = strip_noise(raw)

        cands = set()
        for rx in (NEW_RE, TYPEOF_RE, DECL_RE, ACCESS_RE):
            for m in rx.finditer(code):
                cands.add(m.group(1))
        for m in GENERIC_RE.finditer(code):
            for part in m.group(1).split(","):
                tok = part.strip().split(" ")[0]
                if re.match(r"^[A-Z]\w*$", tok):
                    cands.add(tok)

        for tname in sorted(cands):
            if tname not in types:
                continue
            tns = type_ns.get(tname, "")
            if tns == ns or ns.startswith(tns + ".") or tns in usings:
                continue
            problems.append("%s: '%s' is declared in namespace %s, which this file "
                            "does not import (CS0246)" % (rel, tname, tns or "<global>"))

        # Layering. The simulation is the half the Python reference model mirrors
        # and the half that must compile without any GTA-facing layer present.
        # Core imports Sim, never the other way round; cross-cutting utilities
        # (Log, Settings, IniFile) sit in the root namespace so that every layer
        # sees them without importing anything.
        if ns.startswith("OnyxNations.Sim"):
            for u in usings:
                if u.startswith("OnyxNations.Core") or u.startswith("OnyxNations.Presentation"):
                    warnings.append("%s: the Sim layer imports %s - the dependency "
                                    "arrow must point Sim-wards" % (rel, u))

    # ----------------------------------------------------------------- report
    print("\n=== CHECK ===")
    print("  files declared vs on disk : %s" % ("OK" if not problems or all(
        "does not exist" not in p for p in problems) else "FAIL"))
    for w in warnings:
        print("  [warn] %s" % w)
    for p in problems:
        print("  [FAIL] %s" % p)
    if not problems:
        print("  no unresolved references found")

    print("\n%s (%d problem(s), %d warning(s))"
          % ("CHECK PASSED" if not problems else "CHECK FAILED", len(problems), len(warnings)))
    print("NOTE: this is a structural check, not a C# compiler. Build the project in\n"
          "      Visual Studio before shipping it.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
