"""把 prompts/ 里的 /handoff（交接）和 /claim（认领）安装成各家 agent 的技能。

默认只装 Claude Code（~/.claude/skills/<技能名>/SKILL.md）。
--target codex 装成 Codex 自定义提示（~/.codex/prompts/<技能名>.md）；位置以各工具当前文档为准。
--skill 只装其中一个，默认两个都装。
已有同名文件且内容不同时不覆盖，除非加 --force。--dry-run 只显示会写到哪里。
"""
import argparse
from pathlib import Path
import shlex

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    'claude': '~/.claude/skills/{skill}/SKILL.md',
    'codex': '~/.codex/prompts/{skill}.md',
}
SKILLS = ['handoff', 'claim']


def render(home, skill='handoff'):
    cli = shlex.join([str(ROOT / 'bin/project-inbox'), '--home', str(Path(home).resolve())])
    return (ROOT / f'prompts/{skill}-skill.md').read_text(encoding='utf-8').replace('{{CLI}}', cli)


def install(targets, home=ROOT, force=False, dry_run=False, user_home=None, skills=SKILLS):
    results = []
    for skill in skills:
        results += _install(skill, targets, home, force, dry_run, user_home)
    return results


def _install(skill, targets, home, force, dry_run, user_home):
    results = []
    text = render(home, skill)
    for name in targets:
        target = Path(TARGETS[name].format(skill=skill))
        path = target.expanduser() if user_home is None else Path(user_home) / target.relative_to('~')
        if name == 'codex':
            body = text.split('---', 2)[2].lstrip() if text.startswith('---') else text
        else:
            body = text
        if path.exists() and path.read_text(encoding='utf-8') == body:
            results.append(dict(skill=skill, target=name, path=str(path), action='已是最新'))
            continue
        if path.exists() and not force:
            results.append(dict(skill=skill, target=name, path=str(path), action='已有不同内容，未覆盖（确认后加 --force）'))
            continue
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding='utf-8')
        results.append(dict(skill=skill, target=name, path=str(path), action='将写入（预演）' if dry_run else '已写入'))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', action='append', choices=sorted(TARGETS), help='默认只装 claude，可重复')
    parser.add_argument('--skill', action='append', choices=SKILLS, help='默认两个都装，可重复')
    parser.add_argument('--home', default=str(ROOT), help='ProjectInbox 数据目录')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    for row in install(args.target or ['claude'], args.home, args.force, args.dry_run, skills=args.skill or SKILLS):
        print(f"{row['skill']} / {row['target']}: {row['action']} → {row['path']}")
    print('没有 MCP 和技能目录的 agent（pi、DeepSeek harness 等）：把 prompts/ 里对应技能的文字贴进对话即可，它会改用命令行。')


if __name__ == '__main__':
    main()
