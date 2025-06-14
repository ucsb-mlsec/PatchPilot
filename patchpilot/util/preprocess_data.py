import json
import os
import re
from collections import defaultdict

from patchpilot.util.parse_global_var import parse_global_var_from_code
from get_repo_structure.get_repo_structure import (
    get_project_structure_from_scratch,
    parse_python_file,
)


def is_scope(line):
    # TODO: this might not be precise, can improve with syntax parsing
    return line.startswith("class ") or line.strip().startswith("def ")

def is_func_def(line):
    return line.strip().startswith("def ")

def get_func_name(line):
    return line.strip().split("def ")[1].split("(")[0].strip()

def get_indent_level(line):
    return len(line) - len(line.lstrip())

def get_extended_context_intervals(context_intervals, content):
    lines = content.split("\n")
    functions = []
    current_func = None
    current_indent_level = None

    # Identify all functions in the content
    for i, line in enumerate(lines):
        if is_func_def(line):
            if current_func:
                # If a new function starts, the previous one ends
                current_func['end_line'] = i - 1
                functions.append(current_func)
            # Start a new function
            current_indent_level = get_indent_level(line)
            current_func = {'name': get_func_name(line), 'start_line': i, 'end_line': None}
        elif current_func and line.strip() and get_indent_level(line) <= current_indent_level: 
            # If there is a smaller indent, the function ends
            current_func['end_line'] = i - 1
            functions.append(current_func)
            current_func = None

    if current_func:
        # If there's a function still open at the end, close it
        current_func['end_line'] = len(lines) - 1
        functions.append(current_func)
    extended_context_intervals = []

    for interval in context_intervals:
        min_line, max_line = interval
        extended_min_line = min_line
        extended_max_line = max_line

        # Find the function that wraps the interval's min_line
        for func in functions:
            if func['start_line'] <= min_line <= func['end_line']:
                extended_min_line = func['start_line']
                break

        # Find the function that wraps the interval's max_line
        for func in functions:
            if func['start_line'] <= max_line <= func['end_line']:
                extended_max_line = func['end_line']
                break

        extended_context_intervals.append((extended_min_line, extended_max_line))
    return extended_context_intervals


def line_wrap_content(
    content: str,
    context_intervals=None,
    add_space=False,
    no_line_number=False,
    sticky_scroll=False,
):
    """add n| to each line, where n increases"""

    lines = content.split("\n")
    if context_intervals is None or context_intervals == []:
        context_intervals = [(0, len(lines))]
    
    extended_context_intervals = get_extended_context_intervals(context_intervals, content)
    
    for i in range (len(context_intervals)):
        extended_length = (extended_context_intervals[i][1] - extended_context_intervals[i][0]) - (context_intervals[i][1] - context_intervals[i][0]) 
        if extended_length < 30:
            context_intervals[i] = extended_context_intervals[i]

    new_lines = []
    prev_scopes = []
    line_format = "{line}"
    if not no_line_number:
        line_format = (
            "{line_number}|{line}" if not add_space else "{line_number}| {line} "
        )
    for interval in context_intervals:
        min_line, max_line = interval

        if min_line != 0:
            new_lines.append("...")

        scopes = []
        for i, line in enumerate(lines):
            if sticky_scroll:
                # add current line to scope if necessary
                if is_scope(line): # get the definition line of class or function, append this line at the start of the retrieved code 
                    indent_level = len(line) - len(line.lstrip())
                    while scopes and scopes[-1]["indent_level"] >= indent_level:
                        scopes.pop()
                    scopes.append(
                        {"line": line, "line_number": i, "indent_level": indent_level}
                    )

            if min_line != -1 and i < min_line - 1:
                continue
            if sticky_scroll and i == min_line - 1:
                # add scope lines
                last_scope_line = None
                for j, scope_line in enumerate(scopes):
                    # don't repeat previous scopes
                    if (
                        len(prev_scopes) > j
                        and prev_scopes[j]["line_number"] == scope_line["line_number"]
                    ):
                        continue
                    # don't repeat current line
                    if i == scope_line["line_number"]:
                        continue
                    new_lines.append(
                        line_format.format(
                            line_number=scope_line["line_number"] + 1,
                            line=scope_line["line"],
                        )
                    )
                    last_scope_line = scope_line["line_number"]
                if last_scope_line is not None and last_scope_line < i - 1:
                    new_lines.append("...")

            new_lines.append(line_format.format(line_number=i + 1, line=line))
            if max_line != -1 and i >= max_line - 1:
                break
        prev_scopes = scopes

    if max_line != len(lines):
        new_lines.append("...")

    return "\n".join(new_lines)


def merge_intervals(intervals):
    # intervals inclusive
    if not intervals:
        return []

    # Sort the intervals based on the starting value of each tuple
    intervals.sort(key=lambda interval: interval[0])

    merged_intervals = [intervals[0]]

    for current in intervals[1:]:
        last = merged_intervals[-1]

        # Check if there is overlap
        if current[0] <= last[1]:
            # If there is overlap, merge the intervals
            merged_intervals[-1] = (last[0], max(last[1], current[1]))
        else:
            # If there is no overlap, just add the current interval to the result list
            merged_intervals.append(current)

    return merged_intervals


def transfer_arb_locs_to_locs(
    locs,
    structure,
    pred_file,
    context_window=10,
    loc_interval=False,
    fine_grain_only=False,
    remove_line=False,
    file_content="",
) -> tuple[list, list]:
    if structure is None:
        class_info, function_names, file_lines, imports, import_interval = parse_python_file("", file_content)
        structure = {}
        structure[pred_file] = {
            "classes": class_info,
            "functions": function_names,
            "text": file_lines,
            "imports": imports,
            "import_interval": import_interval,
        }

    files, classes, functions = get_full_file_paths_and_classes_and_functions(structure)
    imports = []
    import_interval = []
    for file in files:
        if file[0] == pred_file:
            imports = file[2]
            import_interval = file[3]
            break

    used_globals = []

    line_loc = []
    if isinstance(locs, str):
        # if its a single loc
        locs = [locs]
    # TODO: parse it in advance
    #this only gives us all global vars in a file, but doesn't filter out the ones that are not used in the specific functions
    global_vars = parse_global_var_from_code(file_content) 

    for model_pred_locs in locs:
        current_class_name = ""
        for loc in model_pred_locs.splitlines():
            # handle cases like "class: MyClass.my_method"
            if loc.startswith("class: ") and "." not in loc:
                loc = loc[len("class: ") :].strip()
                relevant_class = [
                    clazz
                    for clazz in classes
                    if clazz["file"] == pred_file and clazz["name"] == loc
                ]

                if len(relevant_class) == 0:
                    print(f"{loc} class could not be found")
                else:
                    line_loc.append(
                        (relevant_class[0]["start_line"], relevant_class[0]["end_line"])
                    )
                    current_class_name = loc

            elif loc.startswith("function: ") or "." in loc:
                full_loc = loc
                loc = loc.split(":", 1)[-1].strip()

                if "." in loc:
                    # assume its a method within a class
                    method_name = loc.split(".")[1]
                    class_name = loc.split(".")[0]

                    relevant_class = [
                        clazz
                        for clazz in classes
                        if clazz["file"] == pred_file and clazz["name"] == class_name
                    ]
                    if len(relevant_class) == 0:
                        print(f"{class_name} class could not be found")
                    else:
                        relevant_method = [
                            method
                            for method in relevant_class[0]["methods"]
                            if method["name"] == method_name
                        ]
                        if len(relevant_method) == 0:
                            print(f"{full_loc} method could not be found")
                        else:
                            line_loc.append(
                                (
                                    relevant_method[0]["start_line"],
                                    relevant_method[0]["end_line"],
                                )
                            )
                            used_globals.extend(relevant_method[0]["used_globals"])

                else:
                    relevant_function = [
                        function
                        for function in functions
                        if function["file"] == pred_file and function["name"] == loc
                    ]
                    if len(relevant_function) == 0:
                        print(f"{loc} function could not be found")
                        if current_class_name != "":
                            # check if its a method
                            relevant_class = [
                                clazz
                                for clazz in classes
                                if clazz["file"] == pred_file
                                and clazz["name"] == current_class_name
                            ]
                            relevant_method = [
                                method
                                for method in relevant_class[0]["methods"]
                                if method["name"] == loc
                            ]
                            if len(relevant_method) == 0:
                                print(f"{loc} method could not be found")
                                # print([method for method in relevant_class[0]['methods']])
                                #
                                # for file_content in files:
                                #     if file_content[0] == pred_file:
                                #         print("\n".join(file_content[1]))
                                #         exit()
                            else:
                                line_loc.append(
                                    (
                                        relevant_method[0]["start_line"],
                                        relevant_method[0]["end_line"],
                                    )
                                )
                                used_globals.extend(relevant_method[0]["used_globals"])
                        else:
                            # look for it in any class
                            relevant_method = []
                            for clazz in classes:
                                if clazz["file"] == pred_file:
                                    relevant_method.extend(
                                        [
                                            method
                                            for method in clazz["methods"]
                                            if method["name"] == loc
                                        ]
                                    )

                            if len(relevant_method) == 1:
                                line_loc.append(
                                    (
                                        relevant_method[0]["start_line"],
                                        relevant_method[0]["end_line"],
                                    )
                                )
                                used_globals.extend(relevant_method[0]["used_globals"])
                    else:
                        line_loc.append(
                            (
                                relevant_function[0]["start_line"],
                                relevant_function[0]["end_line"],
                            )
                        )
                        used_globals.extend(relevant_function[0]["used_globals"])
            elif loc.startswith("line: "):
                if remove_line:
                    # TODO: can recover the corresponding function instead of throwing it away
                    continue
                loc = loc[len("line: ") :].strip().split()[0]
                try:
                    # line_loc.append(int(loc))
                    line_loc.append((int(loc), int(loc)))
                except:
                    continue
            elif loc.startswith("variable:"):
                vars = loc[len("variable:") :].strip().split()
                for v in vars:
                    if v in global_vars:
                        line_loc.append(
                            (global_vars[v]["start_line"], global_vars[v]["end_line"])
                        )
            else:
                if loc.strip():
                    print(f"loc {loc} not recognised")
                # assert False

    # Fine-grained-only loc: Remove intervals that are supersets of another.
    if fine_grain_only:
        filtered_line_loc = []
        for st, en in line_loc:
            if filtered_line_loc:
                last_st, last_en = filtered_line_loc[-1]
                # If the current interval is a more fine-grained loc, remove the superset.
                if last_st <= st and en <= last_en:
                    filtered_line_loc.pop()
            filtered_line_loc.append((st, en))
        line_loc = filtered_line_loc

    # compute max min
    # TODO: think of strategies to do bunched up lines
    # TODO: e.g., we can have multiple code segments (right now, its just one)

    for file_content in files:
        if file_content[0] == pred_file:
            content = file_content[1]
            break

    if len(line_loc) == 0:
        return [], [], [], []

    # compute overlapping locations instead
    if loc_interval:
        contextual_line_loc = []
        for loc in line_loc:
            max_line = min(loc[1] + context_window, len(content))
            min_line = max(loc[0] - context_window, 0)
            contextual_line_loc.append((min_line, max_line))

        context_intervals = merge_intervals(contextual_line_loc)
        extended_context_intervals = get_extended_context_intervals(context_intervals, "\n".join(content))
    
        for i in range (len(context_intervals)):
            extended_length = (extended_context_intervals[i][1] - extended_context_intervals[i][0]) - (context_intervals[i][1] - context_intervals[i][0]) 
            if extended_length < 30:
                context_intervals[i] = extended_context_intervals[i]
        context_intervals = import_interval + context_intervals

        return line_loc, context_intervals, import_interval, used_globals
    else:
        # defaulting to max min
        max_line = min(max([loc[1] for loc in line_loc]) + context_window, len(content))
        min_line = max(min([loc[0] for loc in line_loc]) - context_window, 0)
        context_intervals = [(min_line, max_line)]
        extended_context_intervals = get_extended_context_intervals(context_intervals, "\n".join(content))
    
        for i in range (len(context_intervals)):
            extended_length = (extended_context_intervals[i][1] - extended_context_intervals[i][0]) - (context_intervals[i][1] - context_intervals[i][0]) 
            if extended_length < 30:
                context_intervals[i] = extended_context_intervals[i]

        return line_loc, context_intervals, import_interval, used_globals


def compile_gt_locations(gt_location: dict) -> tuple[list, set, set, set]:
    """mostly serves a way to check what are the gt locations in gt patch"""
    edits = gt_location["edits"]

    lines, classes, methods, functions = [], set(), set(), set()

    adds = set()

    for edit in edits:
        for clazz in edit["class_names"]:
            classes.add(clazz)

        for method in edit["method_names"]:
            methods.add(method)

        for function in edit["function_names"]:
            functions.add(function)

        if edit["type"] == "add":
            adds.add(edit["line"])
        else:
            lines.append(edit["line"])

    # handle the added lines
    add_intervals = [(i, i + 1) for i in adds]
    add_intervals = merge_intervals(add_intervals)
    for st, en in add_intervals:
        lines.append(st)
    lines = list(set(lines))

    # sort the lines
    lines = sorted(lines)

    return lines, classes, methods, functions


def show_project_structure(structure, spacing=0) -> str:
    """pprint the project structure"""

    pp_string = ""

    for key, value in structure.items():
        if "." in key and ".py" not in key:
            continue  # skip none python files
        if "." in key:
            pp_string += " " * spacing + str(key) + "\n"
        else:
            pp_string += " " * spacing + str(key) + "/" + "\n"
        if "classes" not in value:
            pp_string += show_project_structure(value, spacing + 4)

    return pp_string


def filter_out_test_files(structure):
    """filter out test files from the project structure"""
    for key, value in list(structure.items()):
        if key.startswith("test"):
            del structure[key]
        elif isinstance(value, dict):
            filter_out_test_files(value)


def filter_none_python(structure):
    for key, value in list(structure.items()):
        try:
            if (
                not "functions" in value.keys()
                and not "classes" in value.keys()
                and not "text" in value.keys()
                and not "imports" in value.keys()
                and not "import_interval" in value.keys()
            ) or not len(value.keys()) == 5:
                filter_none_python(value)

                if structure[key] == {}:
                    del structure[key]
            else:
                if not key.endswith(".py"):
                    del structure[key]
        except:
            print(value)
            print(value.keys())

            if structure[key] == {}:
                del structure[key]


def filter_proposed_files(proposed_files, repo_structure):
    """
    Filter proposed files against a given repository structure.

    Arguments:
    proposed_files -- list of proposed files with instance IDs
    repo_structure -- list of repository structures with instance IDs

    Returns:
    A list of dictionaries with instance IDs and valid files matching the repository structure.
    """
    instance_to_files = {
        entry["instance_id"]: entry["files"] for entry in proposed_files
    }
    instance_to_structure = {
        entry["instance_id"]: entry["structure"] for entry in repo_structure
    }
    filtered_files = []
    for instance_id, files in instance_to_files.items():
        if instance_id in instance_to_structure:
            repo_files, _, _ = get_full_file_paths_and_classes_and_functions(
                instance_to_structure[instance_id]
            )
            repo_files_set = set(repo_files)
            valid_files = []
            for repo_file in repo_files_set:
                for proposed_file in files:
                    if proposed_file == repo_file.split("/")[-1]:
                        valid_files.append(repo_file)
            if valid_files:
                filtered_files.append(
                    {"instance_id": instance_id, "files": valid_files}
                )
    return filtered_files


def filter_proposed_classes(proposed_classes, repo_structure):
    """
    Filter proposed classes against a given repository structure.

    Arguments:
    proposed_classes -- list of proposed classes with instance IDs
    repo_structure -- list of repository structures with instance IDs

    Returns:
    A list of dictionaries with instance IDs and valid classes matching the repository structure.
    """
    instance_to_classes = {
        entry["instance_id"]: entry["classes"] for entry in proposed_classes
    }
    instance_to_structure = {
        entry["instance_id"]: entry["structure"] for entry in repo_structure
    }
    filtered_classes = []
    for instance_id, classes in instance_to_classes.items():
        if instance_id in instance_to_structure:
            _, repo_classes, _ = get_full_file_paths_and_classes_and_functions(
                instance_to_structure[instance_id]
            )
            repo_classes_set = {clazz["name"]: clazz["file"] for clazz in repo_classes}
            valid_classes = []
            for proposed_class in classes:
                if proposed_class in repo_classes_set:
                    valid_classes.append(
                        {
                            "name": proposed_class,
                            "file": repo_classes_set[proposed_class],
                        }
                    )
            if valid_classes:
                filtered_classes.append(
                    {"instance_id": instance_id, "classes": valid_classes}
                )
    return filtered_classes


def filter_proposed_methods(proposed_methods, repo_structure):
    """
    Filter proposed methods against a given repository structure.

    Arguments:
    proposed_methods -- list of proposed methods with instance IDs
    repo_structure -- list of repository structures with instance IDs

    Returns:
    A list of dictionaries with instance IDs and valid methods matching the repository structure.
    """
    instance_to_methods = {
        entry["instance_id"]: entry["methods"] for entry in proposed_methods
    }
    instance_to_structure = {
        entry["instance_id"]: entry["structure"] for entry in repo_structure
    }
    filtered_methods = []
    for instance_id, methods in instance_to_methods.items():
        if instance_id in instance_to_structure:
            _, repo_classes, _ = get_full_file_paths_and_classes_and_functions(
                instance_to_structure[instance_id]
            )
            valid_methods = []
            for repo_class in repo_classes:
                for method in methods:
                    if method in repo_class["methods"]:
                        valid_methods.append(
                            {
                                "class": repo_class["name"],
                                "method": method,
                                "file": repo_class["file"],
                            }
                        )
            if valid_methods:
                filtered_methods.append(
                    {"instance_id": instance_id, "methods": valid_methods}
                )
    return filtered_methods


def filter_proposed_functions(proposed_functions, repo_structure):
    """
    Filter proposed functions against a given repository structure.

    Arguments:
    proposed_functions -- list of proposed functions with instance IDs
    repo_structure -- list of repository structures with instance IDs

    Returns:
    A list of dictionaries with instance IDs and valid functions matching the repository structure.
    """
    instance_to_functions = {
        entry["instance_id"]: entry["functions"] for entry in proposed_functions
    }
    instance_to_structure = {
        entry["instance_id"]: entry["structure"] for entry in repo_structure
    }
    filtered_functions = []
    for instance_id, functions in instance_to_functions.items():
        if instance_id in instance_to_structure:
            _, _, repo_functions = get_full_file_paths_and_classes_and_functions(
                instance_to_structure[instance_id]
            )
            valid_functions = []
            for repo_function in repo_functions:
                for function in functions:
                    if isinstance(
                        repo_function["name"], dict
                    ):  # Why are there cases where this is not a dict?
                        if function == repo_function["name"].get("name", []):
                            valid_functions.append(
                                {"function": function, "file": repo_function["file"]}
                            )
            if valid_functions:
                filtered_functions.append(
                    {"instance_id": instance_id, "functions": valid_functions}
                )
    return filtered_functions


def get_full_file_paths_and_classes_and_functions(structure, current_path=""):
    """
    Recursively retrieve all file paths, classes, and functions within a directory structure.

    Arguments:
    structure -- a dictionary representing the directory structure
    current_path -- the path accumulated so far, used during recursion (default="")

    Returns:
    A tuple containing:
    - files: list of full file paths
    - classes: list of class details with file paths
    - functions: list of function details with file paths
    """
    files = []
    classes = []
    functions = []
    for name, content in structure.items():
        if isinstance(content, dict):
            if (
                not "functions" in content.keys()
                and not "classes" in content.keys()
                and not "text" in content.keys()
                and not "imports" in content.keys()
                and not "import_interval" in content.keys()
            ) or not len(content.keys()) == 5:
                # or guards against case where functions and classes are somehow part of the structure.
                next_path = f"{current_path}/{name}" if current_path else name
                (
                    sub_files,
                    sub_classes,
                    sub_functions,
                ) = get_full_file_paths_and_classes_and_functions(content, next_path)
                files.extend(sub_files)
                classes.extend(sub_classes)
                functions.extend(sub_functions)
            else:
                next_path = f"{current_path}/{name}" if current_path else name
                files.append((next_path, content["text"], content.get("imports", []), content.get("import_interval", [])))
                if "classes" in content:
                    for clazz in content["classes"]:
                        classes.append(
                            {
                                "file": next_path,
                                "name": clazz["name"],
                                "start_line": clazz["start_line"],
                                "end_line": clazz["end_line"],
                                "methods": [
                                    {
                                        "name": method["name"],
                                        "start_line": method["start_line"],
                                        "end_line": method["end_line"],
                                        "used_globals": method.get("used_globals",{}),
                                    }
                                    for method in clazz.get("methods", [])
                                ],
                            }
                        )
                if "functions" in content:
                    for function in content["functions"]:
                        function["file"] = next_path
                        functions.append(function)
        else:
            next_path = f"{current_path}/{name}" if current_path else name
            files.append(next_path)

    return files, classes, functions


def find_definitions_by_name(target_name, structure):
    """
    Given a target function or method name, return all definitions (file, start_line, end_line).

    Arguments:
    - target_name: str, the name of the function or method to search for
    - structure: dict, the project structure containing file paths, classes, and functions

    Returns:
    - List of dicts with keys: 'name', 'file', 'start_line', 'end_line', 'type' ('function' or 'method')
    """
    results = []
    _, classes, functions = get_full_file_paths_and_classes_and_functions(structure)

    # Search free functions
    for fn in functions:
        if fn.get("name") == target_name:
            results.append({
                "type": "function",
                "name": target_name,
                "file": fn.get("file"),
                "start_line": fn.get("start_line"),
                "end_line": fn.get("end_line"),
            })

    # Search methods inside classes
    for cls in classes:
        for method in cls.get("methods", []):
            if method.get("name") == target_name:
                results.append({
                    "type": "method",
                    "class": cls.get("name"),
                    "name": target_name,
                    "file": cls.get("file"),
                    "start_line": method.get("start_line"),
                    "end_line": method.get("end_line"),
                })

    return results


def find_callers_by_name(target_name, structure):
    files, classes, functions = get_full_file_paths_and_classes_and_functions(structure)
    callers = []
    seen = set()
    # Iterate through files with content
    for entry in files:
        if isinstance(entry, tuple) and len(entry) >= 2:
            file_path, text_lines, *_ = entry
            for idx, line in enumerate(text_lines):
                # Skip the definition line of the target function/method
                if re.search(rf"\bdef\s+{re.escape(target_name)}\s*\(", line):
                    continue
                # Match calls like target_name(
                if re.search(rf"\b{re.escape(target_name)}\s*\(", line):
                    call_line = idx + 1
                    found = False
                    # Check within free functions
                    for fn in functions:
                        if fn.get("file") == file_path and fn.get("start_line") <= call_line <= fn.get("end_line"):
                            key = ("function", fn["name"], file_path, fn.get("start_line"), fn.get("end_line"))
                            if key not in seen:
                                seen.add(key)
                                callers.append({
                                    "type": "function",
                                    "caller_name": fn.get("name", ""),
                                    "file": file_path,
                                    "start_line": fn.get("start_line", 0),
                                    "end_line": fn.get("end_line", 0),
                                })
                            found = True
                            break
                    if found:
                        continue
                    # Check within class methods
                    for cls in classes:
                        if cls.get("file") == file_path:
                            for method in cls.get("methods", []):
                                if method.get("start_line") <= call_line <= method.get("end_line"):
                                    key = ("method", cls.get("name"), method.get("name"), file_path, method.get("start_line"), method.get("end_line"))
                                    if key not in seen:
                                        seen.add(key)
                                        callers.append({
                                            "type": "method",
                                            "class": cls.get("name"),
                                            "caller_name": method.get("name"),
                                            "file": file_path,
                                            "start_line": method.get("start_line"),
                                            "end_line": method.get("end_line"),
                                        })
                                    found = True
                                    break
                            if found:
                                break
                    if found:
                        continue
                    # Global scope call
                    key = ("global", file_path, call_line, call_line)
                    if key not in seen:
                        seen.add(key)
                        callers.append({
                            "type": "global",
                            "file": file_path,
                            "start_line": call_line,
                            "end_line": call_line,
                        })
    return callers


def find_modified_functions(diff, structure):
    _, classes, functions = get_full_file_paths_and_classes_and_functions(structure)

    file_to_modified_lines = parse_diff_to_modified_lines(diff)

    file_to_classes = defaultdict(list)
    for cls in classes:
        file_to_classes[cls['file']].append(cls)

    file_to_functions = defaultdict(list)
    for func in functions:
        file_to_functions[func['file']].append(func)

    modified_functions = set()

    for file_name, modified_lines in file_to_modified_lines.items():
        for func in file_to_functions.get(file_name, []):
            if any(func['start_line'] <= line <= func['end_line'] for line in modified_lines):
                modified_functions.add(func['name'])

        for cls in file_to_classes.get(file_name, []):
            for method in cls.get('methods', []):
                if any(method['start_line'] <= line <= method['end_line'] for line in modified_lines):
                    modified_functions.add(f"{cls['name']}.{method['name']}")

    return sorted(modified_functions)

def parse_diff_to_modified_lines(diff_text):
    file_changes = defaultdict(set)

    current_file = None
    current_dst_line = None

    for line in diff_text.splitlines():
        if line.startswith('+++ '):
            path = line[4:].strip()
            if path.startswith('b/'):
                path = path[2:]  
            current_file = path
        elif line.startswith('@@'):
            # @@ -start,count +start,count @@
            m = re.search(r'\+(\d+)', line)
            if m:
                current_dst_line = int(m.group(1)) - 1 
        elif line.startswith('+') and not line.startswith('+++'):
            if current_file and current_dst_line is not None:
                current_dst_line += 1
                file_changes[current_file].add(current_dst_line)
        elif not line.startswith('-'):
            if current_dst_line is not None:
                current_dst_line += 1 
    return file_changes


def extract_file_content(files, file_name, start_line, end_line):
    for f_name, source_lines, *_ in files:
        if f_name == file_name:
            return "\n".join(source_lines[start_line-1:end_line])
    raise ValueError(f"File {file_name} not found in provided files.")

PROJECT_FILE_LOC = os.environ.get("PROJECT_FILE_LOC", None)


def get_repo_structure(instance_id: str, repo_name, base_commit, playground, **kwargs):


    # if PROJECT_FILE_LOC is not None:
    #     with open(PROJECT_FILE_LOC + "/" + instance_id + ".json") as f:
    #         d = json.load(f)
    #     repo_structure = d["structure"]
    # else:
    d = get_project_structure_from_scratch(
        repo_name, base_commit, instance_id, playground, **kwargs
    )
    repo_structure = d["structure"]

    return repo_structure


def get_repo_files(structure, filepaths: list[str]):
    files, classes, functions = get_full_file_paths_and_classes_and_functions(structure)
    file_contents = dict()
    for filepath in filepaths:
        content = None

        for file_content in files:
            if file_content[0] == filepath:
                content = "\n".join(file_content[1])
                file_contents[filepath] = content
                break

        assert content is not None, "file not found"
    return file_contents


def correct_file_paths(model_found_files, files, include_partial_paths=True):
    found_files = []
    if model_found_files:
        for model_file in model_found_files:
            if not model_file:
                continue
            found_match = False
            # Check if any model found file is a subset of the current file path
            for file_content in files:
                file = file_content[0]
                if model_file == file:
                    found_files.append(file)
                    found_match = True
            if include_partial_paths and not found_match:
                for file_content in files:
                    file = file_content[0]
                    if file.endswith(model_file) or model_file.endswith(file):
                        found_files.append(file)
                        break  # No need to check further, we found a match
        return found_files
    else:
        return []


def test_correct_file_paths():
    # Test case 1: Exact match
    model_files1 = ["data.txt", "analysis/report.pdf"]
    files1 = [("data.txt",), ("notes.txt",), ("report/report.pdf",)]
    result1 = correct_file_paths(model_files1, files1)
    assert result1 == ["data.txt"], f"Expected ['data.txt'], but got {result1}"

    # Test case 2: Subdirectories in model files
    model_files2 = ["subdir/data.txt", "notes/info.txt"]
    files2 = [
        ("work/subdir/data.txt",),
        ("notes/info.txt",),
        ("extras/notes/info.txt",),
    ]
    result2 = correct_file_paths(model_files2, files2)
    assert result2 == [
        "work/subdir/data.txt",
        "notes/info.txt",
    ], f"Expected ['work/subdir/data.txt', 'notes/info.txt'], but got {result2}"

    # Test case 3: No match
    model_files3 = ["missing.txt"]
    files3 = [("data.txt",), ("notes.txt",), ("analysis/report.pdf",)]
    result3 = correct_file_paths(model_files3, files3)
    assert result3 == [], f"Expected [], but got {result3}"

    # Test case 4: Multiple potential matches but only first match counts
    model_files4 = ["report.doc"]
    files4 = [("work/report.doc",), ("work/rr/report.docg",)]
    result4 = correct_file_paths(model_files4, files4)
    assert result4 == [
        "work/report.doc"
    ], f"Expected ['work/report.doc'], but got {result4}"

    # Test case 5: Model file is a subset
    model_files5 = ["data"]
    files5 = [("project/data_analysis/data.txt",), ("data/config.yaml",)]
    result5 = correct_file_paths(model_files5, files5)
    assert result5 == [], f"Expected [], but got {result5}"

    # Test case 6: File without any folders with two matches
    model_files6 = ["data.txt"]
    files6 = [("project/data_analysis/data.txt",), ("data/config.yaml",), ("data.txt",)]
    result6 = correct_file_paths(model_files6, files6)
    assert result6 == ["data.txt"], f"Expected ['data.txt'], but got {result6}"

    # Test case 7: File without any folders with only a subdirectory match
    model_files7 = ["data.txt"]
    files7 = [("project/data_analysis/data.txt",), ("data/config.yaml",)]
    result7 = correct_file_paths(model_files7, files7)
    assert result7 == [
        "project/data_analysis/data.txt"
    ], f"Expected ['project/data_analysis/data.txt'], but got {result7}"

    print("All test cases passed!")


def test_merge():
    # Example usage:
    input_tuples = [(1, 3), (2, 4), (5, 7), (6, 8)]
    merged_tuples = merge_intervals(input_tuples)
    assert merged_tuples == [(1, 4), (5, 8)]

    input_tuples = [(1, 5), (2, 3)]
    merged_tuples = merge_intervals(input_tuples)
    assert merged_tuples == [(1, 5)]

    input_tuples = [(1, 1)]
    merged_tuples = merge_intervals(input_tuples)
    assert merged_tuples == [(1, 1)]

    input_tuples = [(1, 1), (2, 3)]
    merged_tuples = merge_intervals(input_tuples)
    assert merged_tuples == [(1, 1), (2, 3)]


def test_merge():
    # Example usage:
    input_tuples = [(1, 3), (2, 4), (5, 7), (6, 8)]
    merged_tuples = merge_intervals(input_tuples)
    assert merged_tuples == [(1, 4), (5, 8)]

    input_tuples = [(1, 5), (2, 3)]
    merged_tuples = merge_intervals(input_tuples)
    assert merged_tuples == [(1, 5)]

    input_tuples = [(1, 1)]
    merged_tuples = merge_intervals(input_tuples)
    assert merged_tuples == [(1, 1)]

    input_tuples = [(1, 1), (2, 3)]
    merged_tuples = merge_intervals(input_tuples)
    assert merged_tuples == [(1, 1), (2, 3)]


def test_interval_display():

    content = """
one
two
three
four
five
six
seven
eight
""".strip()

    x = line_wrap_content(content, [])
    print(x)

    print("============")

    x = line_wrap_content(content, [(1, 2), (4, 6), (7, 8)])
    print(x)


if __name__ == "__main__":
    test_merge()
    test_correct_file_paths()
    # test_interval_display()
