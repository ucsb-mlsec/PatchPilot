import ast
import copy
import os
import re
import subprocess
import uuid
from collections import OrderedDict

from patchpilot.util.preprocess_data import get_repo_files
from get_repo_structure.get_patch_info import parse_patch 
from get_repo_structure.get_repo_structure import clone_repo, checkout_commit, apply_patch, repo_to_top_folder   


def check_syntax(code):
    if not code.strip():  # Check for cases where the code is empty or invalid
        return False, "Code is empty or invalid."

    try:
        ast.parse(code)
    except SyntaxError as e:
        # Return False and the error details
        return False, f"SyntaxError: {e.msg}"
    return True, ""


def remove_empty_lines(code: str) -> str:
    # Split the code into lines
    lines = code.splitlines()
    # Remove empty lines
    filtered_lines = [line for line in lines if line.strip() != ""]
    return "\n".join(filtered_lines)


def check_code_differ_by_just_empty_lines(codes, prev_codes) -> bool:

    if not isinstance(codes, list):
        codes = [codes]
        prev_codes = [prev_codes]

    normalized_code1 = ""
    normalized_code2 = ""

    for code, prev_code in zip(codes, prev_codes):
        # Normalize both code snippets
        normalized_code1 += remove_empty_lines(code)
        normalized_code2 += remove_empty_lines(prev_code)

    return normalized_code1 == normalized_code2


def lint_code(repo_playground, temp_name, code, prev_code="") -> tuple[bool, set, set]:

    # Generate a temperary folder and add uuid to avoid collision
    repo_playground = os.path.join(repo_playground, str(uuid.uuid4()))

    # assert playground doesn't exist
    assert not os.path.exists(repo_playground), f"{repo_playground} already exists"

    # create playground
    os.makedirs(repo_playground)

    with open(f"{repo_playground}/{temp_name}", "w") as f:
        f.write(prev_code)

    # lint the code
    # check for fatal errors
    fatal = "E9,F821,F823,F831,F406,F407,F701,F702,F704,F706"
    o = subprocess.run(
        f"flake8 --select={fatal} --isolated {repo_playground}/{temp_name}",
        shell=True,
        capture_output=True,
    )
    s = o.stdout.decode("utf-8")

    prev_errors = set()
    if s != "":
        for error in s.split(f"{repo_playground}/{temp_name}:")[1:]:
            num_free_error = ":".join(error.split(":")[2:]).strip()
            prev_errors.add(num_free_error)

    with open(f"{repo_playground}/{temp_name}", "w") as f:
        f.write(code)

    o = subprocess.run(
        f"flake8 --select={fatal} --isolated {repo_playground}/{temp_name}",
        shell=True,
        capture_output=True,
    )
    s = o.stdout.decode("utf-8")

    # remove playground
    subprocess.run(f"rm -rf {repo_playground}", shell=True)

    errors = set()
    if s != "":
        for error in s.split(f"{repo_playground}/{temp_name}:")[1:]:
            num_free_error = ":".join(error.split(":")[2:]).strip()
            errors.add(num_free_error)

    if len(errors - prev_errors) > 0:
        return False, prev_errors, errors

    return True, set(), set()


def get_diff_real_git_repo(repo_path, file_to_contents, repo_name, commit_id, base_patch_diff='') -> str:
    """create a fake git repo to obtain git diff format"""

    # Generate a temperary folder and add uuid to avoid collision
    repo_playground = os.path.join(repo_path, str(uuid.uuid4()))

    # assert playground doesn't exist
    assert not os.path.exists(repo_playground), f"{repo_playground} already exists"

    # create playground
    os.makedirs(repo_playground)

    clone_repo(repo_name, repo_playground)
    checkout_commit(f"{repo_playground}/{repo_to_top_folder[repo_name]}", commit_id)
    # apply base_patch_diff
    if base_patch_diff:
        apply_patch(f"{repo_playground}/{repo_to_top_folder[repo_name]}", base_patch_diff)

    for file_name, content in file_to_contents.items():
        with open(f"{repo_playground}/{repo_to_top_folder[repo_name]}/{file_name}", "w") as f:
            f.write(content)
    # don't do try-catching here, let the error propagate for debugging purposes

    # get git diff
    o = subprocess.run(
        f"cd {repo_playground}/{repo_to_top_folder[repo_name]} && git diff", shell=True, capture_output=True
    )

    s = o.stdout.decode("utf-8")

    # remove playground
    subprocess.run(f"rm -rf {repo_playground}", shell=True)

    return s


def fake_git_repo(repo_playground, file_pathes, old_contents, new_contents) -> str:
    """create a fake git repo to obtain git diff format"""

    if not isinstance(file_pathes, list):
        # for backwards compatibility
        file_pathes = [file_pathes]
        old_contents = [old_contents]
        new_contents = [new_contents]

    # Generate a temperary folder and add uuid to avoid collision
    repo_playground = os.path.join(repo_playground, str(uuid.uuid4()))

    # assert playground doesn't exist
    assert not os.path.exists(repo_playground), f"{repo_playground} already exists"

    # create playground
    os.makedirs(repo_playground)

    # create a fake git repo
    subprocess.run(f"cd {repo_playground} && git init", shell=True)

    for file_path, old_content, new_content in zip(
        file_pathes, old_contents, new_contents
    ):
        # create a file
        subprocess.run(
            f"mkdir -p {repo_playground}/{os.path.dirname(file_path)}", shell=True
        )

        with open(f"{repo_playground}/{file_path}", "w") as f:
            f.write(old_content)

        # add file to git
        # same message is okay
        subprocess.run(
            f"cd {repo_playground} && git add {file_path} && git commit -m 'initial commit'",
            shell=True,
        )

    for file_path, old_content, new_content in zip(
        file_pathes, old_contents, new_contents
    ):
        # edit file
        with open(f"{repo_playground}/{file_path}", "w") as f:
            f.write(new_content)

    # get git diff
    o = subprocess.run(
        f"cd {repo_playground} && git diff .", shell=True, capture_output=True
    )

    s = o.stdout.decode("utf-8")

    # remove playground
    subprocess.run(f"rm -rf {repo_playground}", shell=True)

    return s


def fake_git_apply(repo_playground, file_path, old_content, patch) -> str:
    """create a fake git repo to obtain new file content"""

    # Generate a temperary folder and add uuid to avoid collision
    repo_playground = os.path.join(repo_playground, str(uuid.uuid4()))

    # assert playground doesn't exist
    assert not os.path.exists(repo_playground), f"{repo_playground} already exists"

    # create playground
    os.makedirs(repo_playground)

    # create a fake git repo
    subprocess.run(f"cd {repo_playground} && git init", shell=True)

    # create a file
    subprocess.run(
        f"mkdir -p {repo_playground}/{os.path.dirname(file_path)}", shell=True
    )

    with open(f"{repo_playground}/{file_path}", "w") as f:
        f.write(old_content)

    # add file to git
    subprocess.run(
        f"cd {repo_playground} && git add {file_path} && git commit -m 'initial commit'",
        shell=True,
    )

    # apply patch file
    patch_file = f"{str(uuid.uuid4())}.patch"
    with open(f"{repo_playground}/{patch_file}", "w") as f:
        f.write(patch)
    o = subprocess.run(
        f"cd {repo_playground} && git apply --whitespace=nowarn {patch_file}",
        shell=True,
        capture_output=True,
    )
    if o.stderr.decode("utf-8"):
        print("stderr> ", o.stderr.decode("utf-8"))
        # TODO: This rarely happen but the patch should be valid, needs to look into it

        with open(f"{repo_playground}/{file_path}", "w") as f:
            f.write(old_content + "\n")

        o = subprocess.run(
            f"cd {repo_playground} && git apply --whitespace=nowarn {patch_file}",
            shell=True,
            capture_output=True,
        )

        if o.stderr.decode("utf-8"):
            print("stderr> ", o.stderr.decode("utf-8"))
            assert False, "shouldn't happen"

    # get git diff
    o = subprocess.run(
        f"cd {repo_playground} && cat {file_path}", shell=True, capture_output=True
    )

    s = o.stdout.decode("utf-8")

    # remove playground
    subprocess.run(f"rm -rf {repo_playground}", shell=True)

    return s


def get_functions(tree):
    """Get a set of function and method names from the AST tree."""
    functions = {}

    class FunctionVisitor(ast.NodeVisitor):
        def __init__(self):
            self.parents = []

        def visit(self, node):
            self.parents.append(node)
            super().visit(node)
            self.parents.pop()

        def visit_FunctionDef(self, node):
            if not any(isinstance(parent, ast.ClassDef) for parent in self.parents):
                functions[node.name] = ast.unparse(node)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            if not any(isinstance(parent, ast.ClassDef) for parent in self.parents):
                functions[node.name] = ast.unparse(node)
            self.generic_visit(node)

    class ClassVisitor(ast.NodeVisitor):
        def visit_ClassDef(self, node):
            class_name = node.name
            for body_item in node.body:
                if isinstance(body_item, ast.FunctionDef) or isinstance(
                    body_item, ast.AsyncFunctionDef
                ):
                    functions[f"{class_name}.{body_item.name}"] = ast.unparse(body_item)
            self.generic_visit(node)

    FunctionVisitor().visit(tree)
    ClassVisitor().visit(tree)
    return functions


def is_just_new_function(code1, code2):
    tree1 = ast.parse(code1)
    tree2 = ast.parse(code2)

    functions1 = get_functions(tree1)
    functions2 = get_functions(tree2)

    # The new functions in the second code
    if len(set(list(functions1.keys())) - set(list(functions2.keys()))) > 0:
        # removes functions
        return False

    for func in functions1:
        if functions1[func] != functions2[func]:
            # modifies existing functions
            return False

    if len(set(list(functions2.keys())) - set(list(functions1.keys()))) > 0:
        return True

    # modifying global stuff is okay, because its actually same as functions almost.

    return False


import io
import re
import tokenize


def remove_comments_and_docstrings(source):
    io_obj = io.StringIO(source)
    out = ""
    prev_toktype = tokenize.INDENT
    last_lineno = -1
    last_col = 0
    for tok in tokenize.generate_tokens(io_obj.readline):
        token_type = tok[0]
        token_string = tok[1]
        start_line, start_col = tok[2]
        end_line, end_col = tok[3]
        ltext = tok[4]
        if start_line > last_lineno:
            last_col = 0
        if start_col > last_col:
            out += " " * (start_col - last_col)
        if token_type == tokenize.COMMENT:
            pass
        elif token_type == tokenize.STRING:
            if prev_toktype != tokenize.INDENT:
                if prev_toktype != tokenize.NEWLINE:
                    if start_col > 0:
                        out += token_string
        else:
            out += token_string
        prev_toktype = token_type
        last_col = end_col
        last_lineno = end_line
    out = "\n".join(l for l in out.splitlines() if l.strip())
    return out



def extract_python_blocks(text):
    # Regular expression pattern to match ```python\n{text}\n```
    pattern = r"```python\n(.*?)\n```"

    # Use re.findall to find all matches
    matches = re.findall(pattern, text, re.DOTALL)

    return matches


def extract_code_blocks(text):
    pattern = r"```\n(.*?)\n```"
    matches = re.findall(pattern, text, re.DOTALL)
    if len(matches) == 0:
        if "```" in text:
            # handle the case where the code block is not complete
            return [text.split("```", 1)[-1].strip()]
    return matches


def extract_locs_for_files(locs, file_names):
    # TODO: keep the order from this fine-grained FL results.
    results = {fn: [] for fn in file_names}
    current_file_name = None
    for loc in locs:
        for line in loc.splitlines():
            if line.strip().endswith(".py"):
                current_file_name = line.strip()
            elif line.strip() and any(
                line.startswith(w)
                for w in ["line:", "function:", "class:", "variable:"]
            ):
                if current_file_name in results:
                    results[current_file_name].append(line)
                else:
                    pass
    return [["\n".join(results[fn])] for fn in file_names]


def extract_starting_number(subcommand):
    return int(subcommand.split(",")[0].split("start=")[-1])


def extract_ending_number(subcommand):
    return int(subcommand.split(",")[1].split("end=")[-1])


def overlap(subcommand1, subcommand2):
    start1, end1 = extract_starting_number(subcommand1), extract_ending_number(
        subcommand1
    )
    start2, end2 = extract_starting_number(subcommand2), extract_ending_number(
        subcommand2
    )
    return not (end1 < start2 or end2 < start1)


def split_edit_multifile_commands(commands, diff_format=False) -> dict[str, str]:
    """Split commands based on edited files."""
    file_to_commands = OrderedDict()
    if diff_format:
        for command in commands:
            file_name = None
            for subcommand in command.split(">>>>>>> REPLACE")[:-1]:
                subcommand = subcommand.strip()
                if "<<<<<<< SEARCH" in subcommand:
                    fn = subcommand.split("<<<<<<< SEARCH")[0].lstrip("#").strip()
                    if fn:
                        file_name = "'" + fn + "'"

                if len(subcommand.split("<<<<<<< SEARCH")) != 2:
                    continue
                converted_command = (
                    "<<<<<<< SEARCH"
                    + subcommand.split("<<<<<<< SEARCH")[1]
                    + "\n"
                    + ">>>>>>> REPLACE"
                )
                # deduplicate
                if (
                    file_name not in file_to_commands
                    or converted_command not in file_to_commands[file_name]
                ):
                    file_to_commands.setdefault(file_name, []).append(converted_command)
    else:
        for command in commands:
            for subcommand in command.split("edit_file(")[1:]:
                file_name, start, end, content = subcommand.split(",", 3)
                converted_command = "edit_file(" + ",".join([start, end, content])
                # deduplicate
                if (
                    file_name not in file_to_commands
                    or converted_command not in file_to_commands[file_name]
                ):
                    file_to_commands.setdefault(file_name, []).append(converted_command)
    return file_to_commands

def check_and_extend_intervals(intervals, buffer=30):
    """
    Extend each interval by 'buffer' (ensuring the start doesn't go below 0),
    then merge intervals that overlap.

    :param intervals: List of tuples, each tuple is an (start, end) interval.
    :param buffer: Amount to extend both ends of each interval (default 30).
    :return: (merged_intervals)
             merged_intervals is the list of merged intervals after extension.
    """

    # 1. Extend intervals and ensure the start >= 0
    extended_intervals = [
        (max(start - buffer, 0), end + buffer) for start, end in intervals
    ]

    # Sort by the start of each interval
    extended_intervals.sort()

    # If there are no intervals, just return
    if not extended_intervals:
        return False, []

    merged_intervals = [extended_intervals[0]]

    # 2. Merge overlapping intervals
    for i in range(1, len(extended_intervals)):
        curr_start, curr_end = extended_intervals[i]
        prev_start, prev_end = merged_intervals[-1]

        # Check if there is overlap
        if curr_start <= prev_end:
            # Merge the two intervals
            merged_intervals[-1] = (prev_start, max(prev_end, curr_end))
        else:
            # No overlap, just add to the list
            merged_intervals.append((curr_start, curr_end))

    return merged_intervals

    

def parse_diff_edit_commands(
    commands, content, file_loc_intervals_file: list[tuple[int, int]]
):
    def parse_for_threedots(original, replace, file_loc_intervals_file, content):
        # if dot dot dot in replace, its always safe to remove it
        if replace.startswith("...\n") and len(replace) > 4:
            # im parsing this first because its used later on
            replace = replace[4:]

        # just dot dot dot, then need to do something special
        if original == "...":
            if not replace[0].isspace():
                # this is okay
                # find a suitable original string to replace
                for interval in file_loc_intervals_file:
                    start, end = interval
                    start = max(start - 1, 0)
                    context_segment = "\n".join(content.splitlines()[start:end])

                    for line in context_segment.splitlines():
                        if len(line) > 0 and not line[0].isspace():
                            if content.count(line) == 1:
                                original = line
                                # keep the line
                                replace = replace + "\n\n" + line
                                break

                    if original != "...":
                        break

                if original == "...":
                    print("cannot find suitable location")

        # dot dot dot with something else in original, then its safe to replace
        if original.startswith("...\n") and len(original) > 4:
            # remove dot dot dot from original
            original = original[4:]

        return original, replace

    # Dedent 'original' by removing common leading whitespace
    def dedent_lines(lines):
        # Find the minimum indentation
        min_indent = None
        for line in lines:
            stripped_line = line.lstrip()
            if stripped_line:
                indent = len(line) - len(stripped_line)
                if min_indent is None or indent < min_indent:
                    min_indent = indent
        # Remove the minimum indentation
        dedented = []
        for line in lines:
            dedented.append(line[min_indent:] if min_indent else line)
        return dedented

    # let's first make sure the intervals are sorted
    file_loc_intervals_file.sort()
    file_loc_intervals_file_copy = copy.deepcopy(file_loc_intervals_file)
    replaced = False
    # apply the edits from the end of file to the beginning of file
    # this is to make sure context is correct
    file_loc_intervals_file_copy = check_and_extend_intervals(file_loc_intervals_file_copy)
    for interval in file_loc_intervals_file_copy[::-1]:
        start, end = interval
        start = start
        end = end
        start = max(start - 1, 0)
        context_segment = "\n".join(content.splitlines()[start:end])
        context_segment = "\n" + context_segment + "\n"

        # since we want to replace the original context, let's first check for all edits.
        while True:
            can_apply = []
            already_applied = []
            for orig_subcommand in commands:
                if not orig_subcommand.startswith("<<<<<<< SEARCH") and orig_subcommand.endswith(
                    ">>>>>>> REPLACE"
                ):
                    continue

                subcommand = "\n".join(orig_subcommand.splitlines()[1:-1])
                if len(subcommand.split("\n=======\n")) != 2:
                    continue

                original, replace = subcommand.split("\n=======\n")

                original, replace = parse_for_threedots(
                    original, replace, file_loc_intervals_file_copy, content
                )

                original = "\n" + original + "\n"
                replace = "\n" + replace + "\n"

                original_lines = original.strip('\n').splitlines()
                replace_lines = replace.strip('\n').splitlines()

                dedented_original = dedent_lines(original_lines)

                if original in context_segment:
                    can_apply.append(subcommand)
                    already_applied.append(orig_subcommand)
                else:
                    pattern_lines = []
                    for line in dedented_original:
                        escaped_line = re.escape(line.lstrip())
                        pattern_line = r'^[ \t]*' + escaped_line
                        pattern_lines.append(pattern_line)
                    pattern = '\n'.join(pattern_lines)
                    regex = re.compile(pattern, re.MULTILINE)

                    # Search for 'original' in context_segment
                    match = regex.search(context_segment)
                    if match:
                        # Get the exact matched text from context_segment
                        matched_text = match.group(0)
                        adjusted_original = matched_text  # Use the matched text directly

                        # Adjust 'replace' to match the indentation of 'matched_text'
                        # Get the indentation levels from 'matched_text'
                        matched_lines = matched_text.splitlines()

                        leading_spaces_original = len(original_lines[0]) - len(dedented_original[0])
                        leading_spaces_context = len(matched_lines[0]) - len(dedented_original[0])

                        indent_diff = leading_spaces_context - leading_spaces_original

                        adjusted_replace_lines = []
                        for line in replace_lines:
                            if indent_diff > 0:
                                adjusted_replace_lines.append(indent_diff * ' ' + line)
                            else:
                                index = -indent_diff
                                adjusted_replace_lines.append(line[index:])

                        adjusted_replace = '\n'.join(adjusted_replace_lines)

                        subcommand = f"\n{adjusted_original}\n=======\n{adjusted_replace}\n"
                        can_apply.append(subcommand)
                        already_applied.append(orig_subcommand)
                
            # remove already applied edits
            for orig_subcommand in already_applied:
                commands.remove(orig_subcommand)

            if not can_apply:
                break
        
            # apply edits backwards
            for subcommand in can_apply:
                original, replace = subcommand.split("\n=======\n")

                original, replace = parse_for_threedots(
                    original, replace, file_loc_intervals_file_copy, content
                )

                original = "\n" + original + "\n"
                replace = "\n" + replace + "\n"
                if (
                    original.strip('\n') in context_segment
                ):  # This may not be true after some previously applied edits
                    context_segment = context_segment.replace(original.strip('\n'), replace)
                    replaced = True
        # reassembly
        content = (
            "\n".join(content.splitlines()[:start])
            + context_segment
            + "\n".join(content.splitlines()[end:])
        )
    content = content.lstrip()

    if not replaced:
        print("not replaced")
        print("commands: \n", commands)
    return content, replaced


def parse_edit_commands(commands, content):
    content_lines = content.splitlines()
    map_content_lines_to_line_num = [i for i in range(0, len(content_lines) + 1)]

    # Make a list of the subcommands
    subcommands = []

    for command in commands:
        for subcommand in command.split("edit_file(")[1:]:
            subcommands.append(subcommand)

    # Remove duplicates while preserving order
    seen = set()
    unique_subcommands = []
    for subcommand in subcommands:
        if subcommand not in seen:
            unique_subcommands.append(subcommand)
            seen.add(subcommand)

    # Sort the unique subcommands by the starting number in reverse order
    unique_sorted_subcommands = sorted(
        unique_subcommands, key=extract_starting_number, reverse=True
    )

    for subcommand in unique_sorted_subcommands:
        command_start = int(subcommand.split(",")[0].split("start=")[-1])
        command_end = int(subcommand.split(",")[1].split("end=")[-1])

        start = map_content_lines_to_line_num.index(command_start)
        end = map_content_lines_to_line_num.index(command_end)

        try:
            changed_content = eval(
                ")".join(",".join(subcommand.split(",")[2:]).split(")")[:-1])
            )
            # small thing to ensure right white space indent
            if (
                start == end
                and len(changed_content.splitlines()) == 1
                and (len(changed_content) - len(changed_content.lstrip())) == 0
            ):
                indent_length = len(content_lines[start - 1]) - len(
                    content_lines[start - 1].lstrip()
                )
                changed_content = " " * indent_length + changed_content.lstrip()

        # catch syntax error
        except:
            # try to fix, specially for case where there are """ or ''' in the string, that cannot be
            # easily evaluated.
            eval_str = ")".join(
                ",".join(subcommand.split(",")[2:]).split(")")[:-1]
            ).strip()

            if eval_str.startswith("content="):
                eval_str = eval_str[8:]

            if eval_str.startswith('"""') or eval_str.startswith("'''"):
                eval_str = eval_str[3:-3]
            if eval_str.startswith('"') or eval_str.startswith("'"):
                eval_str = eval_str[1:-1]

            changed_content = eval_str

        content_lines[start - 1 : end] = changed_content.splitlines()

    content = "\n".join(content_lines)

    return content


def test_parse():
    raw_output = """
```python
edit_file(1, 1, "import os")
```
"""

    content = """
import sys
""".strip()

    commands = extract_python_blocks(raw_output)

    content = parse_edit_commands(commands, content)

    assert content == "import os", content

    raw_output = """
```python
edit_file(1, 1, '''import os\nimport sys''')
```
"""

    content = """
import sys
""".strip()

    commands = extract_python_blocks(raw_output)

    content = parse_edit_commands(commands, content)

    assert content == "import os\nimport sys", content

    raw_output = """
```python
edit_file(1, 1, '''import os''')
edit_file(1, 1, '''import sys''')
```
"""

    content = """
import sys
""".strip()

    commands = extract_python_blocks(raw_output)

    content = parse_edit_commands(commands, content)

    assert content == "import sys", content

    content = """
test
testing
""".strip()
    raw_output = """
```python
edit_file(1, 1, "testing\ntesting2")
edit_file(2, 2, "testing3")
```
"""
    content = parse_edit_commands(extract_python_blocks(raw_output), content)

    assert content == "testing\ntesting2\ntesting3", content

    content = """
test
testing
testinging
""".strip()

    raw_output = """
```python
edit_file(1, 2, "testing")
edit_file(3, 3, "testing3")
```
"""
    content = parse_edit_commands(extract_python_blocks(raw_output), content)

    assert content == "testing\ntesting3", content

    content = """
test
testing
testinging
test
testing
""".strip()

    raw_output = """
```python
edit_file(1, 2, "testing")
edit_file(3, 3, "testing3")
edit_file(4, 4, "testing\ntesting2")
edit_file(5, 5, "testing3")
```
"""

    edited_content = """
testing
testing3
testing
testing2
testing3
""".strip()
    content = parse_edit_commands(extract_python_blocks(raw_output), content)

    assert content == edited_content, content

    # Test for if command is present twice in the output (adapted from real output)
    content = """
test
testing
testinging
test
testing
""".strip()

    raw_output = (
        """
"To fix the issue where `viewcode`
1. Add a condition

```python
edit_file(4, 4, """
        """
test4
"""
        """)
```

2. Ensure that the `doctree_read`

```python
edit_file(2, 3, """
        """
test-2-3
"""
        """)
```

Here is the complete set of commands to address the issue:

```python
edit_file(4, 4, """
        """
test4
"""
        """)
```

```python
edit_file(2, 3, """
        """
test-2-3
"""
        """)
```
"""
    )
    content = parse_edit_commands(extract_python_blocks(raw_output), content)

    revised_content = """
test
test-2-3
test4
testing
""".strip()

    assert content == revised_content, content

    raw_output = """
```
django/db/migrations/optimizer.py
function: MigrationOptimizer.optimize_inner

django/db/migrations/operations/fields.py
function: AlterField.reduce
```
"""
    files = [
        "django/db/migrations/optimizer.py",
        "django/db/migrations/operations/fields.py",
        "django/db/migrations/operations/models.py",
    ]
    extracted_locs = extract_locs_for_files([raw_output], files)
    print(extracted_locs)
    assert extracted_locs == [
        ["function: MigrationOptimizer.optimize_inner"],
        ["function: AlterField.reduce"],
        [""],
    ]

    raw_output = """
```python
edit_file(start=1, end=1, content="testing not")
```
"""

    content = """
testing
""".strip()

    edited_content = """
testing not
""".strip()

    content = parse_edit_commands(extract_python_blocks(raw_output), content)
    assert content == edited_content, edited_content


if __name__ == "__main__":
    # test_parse()

    # Example Usage
    code1 = """
class MyClass:
    def existing_method(self):
        pass
"""

    code2 = """
class MyClass:
    def existing_method(self):
        pass

    async def new_method(self):
        pass
"""

    print(is_just_new_function(code1, code2))  # Output: True
