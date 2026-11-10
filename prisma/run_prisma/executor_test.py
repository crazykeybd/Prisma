
from collections import defaultdict

from common.util_prisma import init_log, read_config, read_table, resolve_path
from keywords.keyword_lib import execute_command


def execute_testcase(file_path=None):
    logger = init_log()
    config = read_config()
    if file_path is None:
        file_path = resolve_path(config.get("dataConfigPath"))
    commands = read_table(file_path)
    groups = defaultdict(list)
    for command in commands:
        groups[str(command[0])].append(command)
    results = {}
    for case_no, cmds in groups.items():
        success = True
        for command in cmds:
            if not execute_command(command):
                success = False
                break
        results[case_no] = success
    logger.info("summary: %s", results)
    return results


if __name__ == "__main__":
    print(execute_testcase())
