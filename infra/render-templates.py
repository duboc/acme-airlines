#!/usr/bin/env python3
"""Render the agent templates in docs/scion/templates with factory.env + factory.local.env + deploy-config.json.

Usage:
    infra/render-templates.py --out /tmp/scion-templates
    infra/render-templates.py --out /tmp/scion-templates --roles lead,backend,frontend,reviewer,deployer
    infra/render-templates.py --file infra/heartbeat-message.txt
"""
import argparse
import json
import os
import re
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
VARS = (
    "PROJECT_ID REGION HUB_NAME HUB_PROJECT SANDBOX_PROJECT SANDBOX_REGION "
    "DEPLOYER_SA_NAME RUNTIME_SA_NAME DEPLOYER_SA RUNTIME_SA AGENT_SA "
    "SERVING_SERVICE DASHBOARD_SERVICE GITHUB_REPO BUILD_PROJECT EVAL_PROJECT "
    "HUB_PROJECT_SLUG BUILD_BRANCH EVAL_BRANCH ADMIN_EMAIL PO_EMAIL"
).split()
PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def _parse_env_file(path, values):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")


def load_values(env_path, local_env_path, config_path):
    values = {}
    if os.path.exists(config_path):
        cfg = json.load(open(config_path))
        if cfg.get("project_id"):
            values["PROJECT_ID"] = cfg["project_id"]
        if cfg.get("region"):
            values["REGION"] = cfg["region"]
        if cfg.get("hub_name"):
            values["HUB_NAME"] = cfg["hub_name"]
    _parse_env_file(env_path, values)
    _parse_env_file(local_env_path, values)
    for k in VARS:
        if os.environ.get(k):
            values[k] = os.environ[k]

    project_id = values.get("PROJECT_ID", "acme-airlines-factory")
    region = values.get("REGION", "us-central1")
    hub_name = values.get("HUB_NAME", "acme")
    values.setdefault("HUB_PROJECT", project_id)
    values.setdefault("SANDBOX_PROJECT", project_id)
    values.setdefault("SANDBOX_REGION", region)
    values.setdefault("BUILD_PROJECT", values.get("HUB_PROJECT_SLUG", "duboc-acme-airlines"))
    values.setdefault("HUB_PROJECT_SLUG", values["BUILD_PROJECT"])
    values.setdefault("EVAL_PROJECT", "acme-airlines-eval")
    values.setdefault("BUILD_BRANCH", "main")
    values.setdefault("EVAL_BRANCH", "eval-dashboard")
    values.setdefault("SERVING_SERVICE", "acme-irrops-demo")
    values.setdefault("DASHBOARD_SERVICE", "acme-observatory")
    values.setdefault("DEPLOYER_SA_NAME", "acme-observatory-deployer")
    values.setdefault("RUNTIME_SA_NAME", "acme-irrops-demo")
    values.setdefault("DEPLOYER_SA", f"{values['DEPLOYER_SA_NAME']}@{values['SANDBOX_PROJECT']}.iam.gserviceaccount.com")
    values.setdefault("RUNTIME_SA", f"{values['RUNTIME_SA_NAME']}@{values['SANDBOX_PROJECT']}.iam.gserviceaccount.com")
    values.setdefault("AGENT_SA", f"{hub_name}-agent@{project_id}.iam.gserviceaccount.com")
    if values.get("ADMIN_EMAIL") and not values.get("PO_EMAIL"):
        values["PO_EMAIL"] = values["ADMIN_EMAIL"]
    if values.get("PO_EMAIL") and not values.get("ADMIN_EMAIL"):
        values["ADMIN_EMAIL"] = values["PO_EMAIL"]
    return {k: v for k, v in values.items() if k in VARS}


def render(text, values, missing, where):
    def repl(match):
        name = match.group(1)
        if name not in VARS:
            return match.group(0)
        if not values.get(name):
            missing.add(f"{name} (in {where})")
            return match.group(0)
        return values[name]

    return PLACEHOLDER.sub(repl, text)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", default=os.path.join(HERE, "factory.env"))
    parser.add_argument("--local-env", default=os.path.join(HERE, "factory.local.env"))
    parser.add_argument("--config", default=os.path.join(HERE, "deploy-config.json"))
    parser.add_argument("--src", default=os.path.join(REPO_ROOT, "docs", "scion", "templates"))
    parser.add_argument("--out", default="/tmp/scion-templates")
    parser.add_argument("--roles", help="comma-separated subset of roles (default: all)")
    parser.add_argument("--file", help="render this one file to stdout instead of the templates")
    args = parser.parse_args()

    values = load_values(args.env, args.local_env, args.config)
    if args.file:
        missing = set()
        text = render(open(args.file).read(), values, missing, args.file)
        if missing:
            sys.exit("missing values in factory.env / factory.local.env:\n  " + "\n  ".join(sorted(missing)))
        sys.stdout.write(text.rstrip("\n"))
        return 0

    roles = sorted(
        d for d in os.listdir(args.src)
        if os.path.isdir(os.path.join(args.src, d)) and not d.startswith("_")
    )
    if args.roles:
        wanted = [r.strip() for r in args.roles.split(",") if r.strip()]
        unknown = set(wanted) - set(roles)
        if unknown:
            sys.exit(f"unknown roles: {', '.join(sorted(unknown))}")
        roles = wanted

    shared_path = os.path.join(args.src, "_shared", "agents.md")
    shared = open(shared_path).read()

    missing = set()
    os.makedirs(args.out, exist_ok=True)
    for role in roles:
        src_dir, out_dir = os.path.join(args.src, role), os.path.join(args.out, role)
        if os.path.exists(out_dir):
            shutil.rmtree(out_dir)
        for dirpath, _, files in os.walk(src_dir):
            for name in files:
                src = os.path.join(dirpath, name)
                rel = os.path.relpath(src, src_dir)
                if rel in ("agents.md", "agents.extra.md"):
                    continue
                dst = os.path.join(out_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    raw_text = open(src, encoding="utf-8").read()
                    rendered = render(raw_text, values, missing, f"{role}/{rel}")
                    with open(dst, "w", encoding="utf-8") as f:
                        f.write(rendered)
                except UnicodeDecodeError:
                    shutil.copy2(src, dst)
                shutil.copymode(src, dst)
        extra_path = os.path.join(src_dir, "agents.extra.md")
        agents = render(shared, values, missing, "_shared/agents.md")
        if os.path.exists(extra_path):
            agents += "\n" + render(open(extra_path, encoding="utf-8").read(), values, missing, f"{role}/agents.extra.md")
        with open(os.path.join(out_dir, "agents.md"), "w", encoding="utf-8") as f:
            f.write(agents)
        print(f"rendered {role} -> {out_dir}")

    if missing:
        sys.exit("missing values in factory.env / factory.local.env:\n  " + "\n  ".join(sorted(missing)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
