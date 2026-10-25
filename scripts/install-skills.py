#!/usr/bin/env python3
"""Preview or apply canonical profile skill packs through pinned NixKit."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_MANIFEST = ROOT / "skills/packs.json"
HOST_NATIVE_AUTHORIZATION = ROOT / "skills/host-native-approvals/lnx-preserve-host-native-v1.json"
PROFILE_MAP = {"lnx":"default","pulse":"pulse","algua":"algua","meabeclick":"meabeclick","meabeclickux":"meabeclickux","meabeclickeng":"meabeclickeng","meabeclickgrowth":"meabeclickgrowth"}
PROFILES = set(PROFILE_MAP)
NIXKIT_REPOSITORY = "https://github.com/its-nix/kit.git"
NIXKIT_COMMIT = "f5377ca3219daf4f33f6e8525f9438318dabcd2f"
NIXKIT_SNAPSHOT_COMMIT = "da5f36fc4c470fa28ffdcab1a6c6aaa3750bded3"
NIXKIT_SOURCES_SHA256 = "110ecc3872b4fe45ede667afd4ca757d59aef850672045a639eae5ef78415a83"
NIXKIT_INSTALLER_SHA256 = "9748b8338c88c766208fa54fa144c95f0cd282a72f95327f485641ce85c18041"
NIXKIT_LOCAL_TREES = {"skills/watch":"64685da3912a54d5f672c4bdce0e89b69b4cd6c8"}
NODE_RUNTIME = "24.22.1"
EXPECTED_PACKS = {
 "baseline-evidence":["verification-before-completion"],
 "orchestrator-control":["dispatching-parallel-agents","receiving-code-review","handoff"],
 "project-lead-core":["brainstorming","domain-modeling","to-spec","to-tickets","codebase-design","receiving-code-review","handoff"],
 "public-research":[],
 "engineering-delivery":["executing-plans","using-git-worktrees","finishing-a-development-branch","receiving-code-review","codebase-design"],
 "ux-design-review":["frontend-design"],
 "product-development":["lnx-grilling","lnx-grill-me","lnx-domain-modeling","lnx-grill-with-docs","lnx-to-spec","lnx-writing-for-agents","nix-idea-to-mvp","writing-plans","subagent-driven-development"],
}
EXPECTED_ASSIGNMENTS = {
 "lnx":["baseline-evidence","orchestrator-control"], "pulse":["baseline-evidence","project-lead-core","public-research"],
 "algua":["baseline-evidence","project-lead-core","public-research"], "meabeclick":["baseline-evidence","project-lead-core","public-research"],
 "meabeclickux":["baseline-evidence","ux-design-review"], "meabeclickeng":["baseline-evidence","engineering-delivery"],
 "meabeclickgrowth":["baseline-evidence","public-research"],
}
EXPECTED_HOST_NATIVE_SATISFACTION = {
 "lnx":[
  {"skill":"nix-idea-to-mvp","path":"product-development/nix-idea-to-mvp","tree_sha256":"d0594a11fc1ae3e7b852e23f0ea8be40c3273feea903c25a8300e68a2b86db9e"},
  {"skill":"writing-plans","path":"software-development/writing-plans","tree_sha256":"f39910eddd93faa946cffdba88121fbc62bef710ac9087d1221b64a8d7b3da04"},
  {"skill":"subagent-driven-development","path":"software-development/subagent-driven-development","tree_sha256":"e6f083d602c635d8de51ccd5dd0d202cf41e385705cc7a61ead83d05fd17b54f"},
 ],
 "pulse":[], "algua":[], "meabeclick":[], "meabeclickux":[], "meabeclickeng":[], "meabeclickgrowth":[],
}
EXPECTED_HOST_NATIVE_AUTHORIZATION = {
 "schema_version":1,
 "kind":"corrective_host_native_preservation_authorization",
 "profile":"lnx",
 "approved_by":"Lior",
 "evidence":{"platform":"telegram","chat_id":"6926015052","quote":"preserve and amend"},
 "artifacts":EXPECTED_HOST_NATIVE_SATISFACTION["lnx"],
}
LEGACY_NIXKIT_SKILLS = {"using-superpowers","brainstorming","writing-plans","executing-plans","systematic-debugging","test-driven-development","verification-before-completion","requesting-code-review","receiving-code-review","using-git-worktrees","finishing-a-development-branch","subagent-driven-development","dispatching-parallel-agents","diagnosing-superpowers","setup-matt-pocock-skills","grill-with-docs","grilling","domain-modeling","to-spec","to-tickets","codebase-design","research","handoff","caveman","ponytail","frontend-design","web-design-guidelines","agent-browser","remotion-best-practices","auto-editor","auto-editor-effects","auto-editor-export","auto-editor-transcribe","obsidian-markdown","qmd","gws-shared","gws-drive","gws-docs","gws-drive-upload","gws-docs-write","find-docs"}
NIXKIT_SKILLS = LEGACY_NIXKIT_SKILLS | {"lnx-grilling","lnx-grill-me","lnx-domain-modeling","lnx-grill-with-docs","lnx-to-spec","lnx-writing-for-agents","nix-idea-to-mvp","watch","performance","rtl-design","analytics","customer-research","todoist-cli","technical-project-management","web-accessibility","social-media-content-calendar"}
PACK_PROPOSALS = {("lnx","product-development"):"lnx-product-development-92338765"}
PRODUCT_DEVELOPMENT_SKILLS = EXPECTED_PACKS["product-development"]
APPROVAL_AUTHORITY_DISCORD_ID = "454290355320520714"
APPROVAL_CHANNEL_ID = "1563181903253737542"
PRODUCT_REQUEST_EVIDENCE = {"kind":"request/proposal","platform":"discord","author_id":APPROVAL_AUTHORITY_DISCORD_ID,"channel_id":APPROVAL_CHANNEL_ID,"message_id":"1563496182829948941","quote":"sync it","timestamp":"2026-10-24T10:15:38.341000+00:00"}
PRODUCT_PROPOSAL_COMMIT = "083d008d3a83cd178e1f79bf962967ae7054fc0e"
PRODUCT_PROPOSAL_SHA256 = "06db117e1d84486a50f7beaae32f83fc3540ca8fd01eb5dc7fcf0f9e62100288"
PRODUCT_PROPOSAL_MANIFEST_SHA256 = "7a8a7f67f2498f147f03833501b9c1c39e35bd1cee624e3e833061d77a62cf3f"
PRODUCT_NIXKIT = {"repository":NIXKIT_REPOSITORY,"commit":"92338765a1cc942b2a20dda3f1e220d6be0225e1","snapshot_commit":NIXKIT_SNAPSHOT_COMMIT,"sources_sha256":"6493103af689bdf4bae5455e1d901646fe079e51a69c53b6f5bcf57c62786d21"}
PRODUCT_APPROVAL_EVIDENCE = {"kind":"approval","platform":"discord","author_id":APPROVAL_AUTHORITY_DISCORD_ID,"channel_id":APPROVAL_CHANNEL_ID,"message_id":"1563556252829622396","quote":f"approve {PACK_PROPOSALS[('lnx','product-development')]} at {PRODUCT_PROPOSAL_COMMIT}","timestamp":"2026-10-24T14:14:20.145000+00:00"}
HISTORICAL_APPROVAL_MANIFESTS = {
 "b7d9ba7ce3551e428b6f009c744ad3b71f821338": ("3f64702b483666b22468407d4e5c601911c6730f3f2bc182eacbfa2ca446feaa", ["caveman"]),
 "999cc615cd45c11b5ba10f9d02ff837171c68584": ("f143b7a554795331980742ebea23b39e61e13d8bbbd15321b653c28ab1082a30", ["caveman","watch"]),
 "80acfdec871514d5cd3c0d16d36dfb54001768b8": ("d155b871cfafeba7838ca26ad85bd6988a7cf8fefccf367113d4bb3c9202fe70", ["caveman","watch","todoist-cli"]),
}
HARD_GATES = {"relevance","provenance","reliability","safety_privacy","license","hermes_compatibility","non_duplication"}
SCORE_LIMITS = {"role_value":30,"evidence_quality":20,"security_provenance":20,"hermes_compatibility":15,"maintenance":10,"documentation_license":5}
SHA = re.compile(r"^[a-f0-9]{40}$")
SKILL = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROPOSAL_ID = re.compile(r"^[a-z0-9][a-z0-9.-]+$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SKILL_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.{1,2}(?:/|$))[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
MAX_TEXT = 4_096
MAX_URL = 2_048
MAX_EVIDENCE_URLS = 100
MAX_RISKS = 50
HOST_NATIVE_DESCRIPTOR_TRAVERSAL = (
 hasattr(os,"O_NOFOLLOW") and hasattr(os,"O_DIRECTORY") and os.open in os.supports_dir_fd
 and os.stat in os.supports_dir_fd and os.scandir in os.supports_fd
)

class Refusal(RuntimeError): pass

def _run(command:list[str], cwd:Path)->str:
 result=subprocess.run(command,cwd=cwd,text=True,capture_output=True,check=False)
 if result.returncode:
  raise Refusal(f"command failed: {' '.join(command)}: {result.stderr.strip() or result.stdout.strip() or result.returncode}")
 return result.stdout.strip()

def _run_bytes(command:list[str],cwd:Path)->bytes:
 result=subprocess.run(command,cwd=cwd,capture_output=True,check=False)
 if result.returncode:
  detail=result.stderr.decode("utf-8",errors="replace").strip() or result.stdout.decode("utf-8",errors="replace").strip() or str(result.returncode)
  raise Refusal(f"command failed: {' '.join(command)}: {detail}")
 return result.stdout

def _verify_local_tree(checkout:Path,relative:str,expected_tree:str)->None:
 """Require a local source payload to be exactly its pinned Git tree."""
 if _run(["git","rev-parse",f"{NIXKIT_COMMIT}:{relative}"],checkout)!=expected_tree:
  raise Refusal(f"pinned local source Git tree is not the audited object: {relative}")
 raw=_run_bytes(["git","ls-tree","-rz","-r","--full-tree",NIXKIT_COMMIT,"--",relative],checkout)
 expected:dict[str,tuple[str,str]]={}; prefix=relative+"/"
 for record in raw.split(b"\0"):
  if not record: continue
  try:
   header,path_bytes=record.split(b"\t",1); mode,kind,object_id=header.decode("ascii").split(" "); path=path_bytes.decode("utf-8")
  except (ValueError,UnicodeDecodeError) as exc: raise Refusal(f"cannot parse pinned local source Git tree: {relative}") from exc
  if kind!="blob" or mode not in {"100644","100755","120000"} or not path.startswith(prefix): raise Refusal(f"pinned local source Git tree has an unsupported entry: {path}")
  child=path[len(prefix):]
  if not child or child.startswith("/") or any(part in {"",".",".."} for part in child.split("/")) or child in expected: raise Refusal(f"pinned local source Git tree has an invalid path: {path}")
  expected[child]=(mode,object_id)
 if not expected: raise Refusal(f"pinned local source Git tree is empty: {relative}")
 expected_dirs={"/".join(parts[:index]) for child in expected for parts in [child.split("/")] for index in range(1,len(parts))}
 root=checkout/relative
 try:
  root_stat=root.lstat()
 except OSError as exc: raise Refusal(f"cannot inspect pinned local source payload {relative}: {exc}") from exc
 if not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink(): raise Refusal(f"pinned local source payload is not a real directory: {relative}")
 actual:dict[str,os.stat_result]={}
 def walk(directory:Path,prefix_path:str="")->None:
  try: entries=list(os.scandir(directory))
  except OSError as exc: raise Refusal(f"cannot enumerate pinned local source payload {relative}: {exc}") from exc
  for entry in entries:
   child=f"{prefix_path}/{entry.name}" if prefix_path else entry.name
   try: info=entry.stat(follow_symlinks=False)
   except OSError as exc: raise Refusal(f"cannot inspect pinned local source entry {relative}/{child}: {exc}") from exc
   actual[child]=info
   if stat.S_ISDIR(info.st_mode): walk(Path(entry.path),child)
 walk(root)
 if set(actual)!=set(expected)|expected_dirs:
  raise Refusal(f"pinned local source payload file set differs from Git tree: {relative}")
 for directory in expected_dirs:
  if not stat.S_ISDIR(actual[directory].st_mode): raise Refusal(f"pinned local source entry type differs from Git tree: {relative}/{directory}")
 for child,(mode,object_id) in expected.items():
  path=root/child; info=actual[child]; expected_bytes=_run_bytes(["git","cat-file","blob",object_id],checkout)
  if mode=="120000":
   if not stat.S_ISLNK(info.st_mode): raise Refusal(f"pinned local source entry type differs from Git tree: {relative}/{child}")
   try: actual_bytes=os.fsencode(os.readlink(path))
   except OSError as exc: raise Refusal(f"cannot read pinned local source symlink {relative}/{child}: {exc}") from exc
  else:
   if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode): raise Refusal(f"pinned local source entry type differs from Git tree: {relative}/{child}")
   if bool(info.st_mode&0o111)!=(mode=="100755"): raise Refusal(f"pinned local source executable mode differs from Git tree: {relative}/{child}")
   try: actual_bytes=path.read_bytes()
   except OSError as exc: raise Refusal(f"cannot read pinned local source file {relative}/{child}: {exc}") from exc
  if actual_bytes!=expected_bytes: raise Refusal(f"pinned local source bytes differ from Git tree: {relative}/{child}")

def _verify_installer(checkout:Path)->Path:
 """Bind the executable installer bytes to the approved Git object and digest."""
 installer=checkout/"install.mjs"
 try: info=installer.lstat(); actual=installer.read_bytes()
 except OSError as exc: raise Refusal(f"cannot read pinned NixKit install.mjs: {exc}") from exc
 if not stat.S_ISREG(info.st_mode) or installer.is_symlink(): raise Refusal("NixKit install.mjs is missing or a symlink")
 expected=_run_bytes(["git","show",f"{NIXKIT_COMMIT}:install.mjs"],checkout)
 if actual!=expected or hashlib.sha256(actual).hexdigest()!=NIXKIT_INSTALLER_SHA256:
  raise Refusal("NixKit install.mjs bytes do not match the pinned reviewed Git object")
 return installer

def _load_json(path:Path,label:str)->dict:
 try: value=json.loads(path.read_text(encoding="utf-8"))
 except (OSError,UnicodeError,json.JSONDecodeError) as exc: raise Refusal(f"cannot read {label}: {exc}") from exc
 if not isinstance(value,dict): raise Refusal(f"{label} root must be an object")
 return value

def _source_catalog(path:Path)->dict[str,tuple[str,str,str|None]]:
 try: value=json.loads(path.read_text(encoding="utf-8"))
 except (OSError,UnicodeError,json.JSONDecodeError) as exc: raise Refusal(f"cannot parse pinned NixKit sources.json: {exc}") from exc
 if not isinstance(value,list): raise Refusal("pinned NixKit sources.json root must be an array")
 catalog:dict[str,tuple[str,str,str|None]]={}; local_records=0
 for source in value:
  if not isinstance(source,dict): raise Refusal("pinned NixKit sources.json has an invalid source record")
  github_keys=set(source) in ({"repo","ref","skills"},{"repo","ref","path","skills"})
  if github_keys and isinstance(source.get("repo"),str) and REPOSITORY.fullmatch(source["repo"]) and isinstance(source.get("ref"),str) and SHA.fullmatch(source["ref"]) and isinstance(source.get("skills"),list) and ("path" not in source or isinstance(source["path"],str) and SKILL_PATH.fullmatch(source["path"])):
   repository,revision,expected_path=source["repo"],source["ref"],source.get("path")
  elif source=={"local":"skills","skills":["watch"]}:
   local_records+=1; repository,revision,expected_path="its-nix/kit",NIXKIT_COMMIT,"skills/watch"
  else: raise Refusal("pinned NixKit sources.json has an invalid discriminated source record")
  for skill in source["skills"]:
   if not isinstance(skill,str) or not SKILL.fullmatch(skill) or skill in catalog: raise Refusal("pinned NixKit sources.json skill names must be valid and globally unique")
   catalog[skill]=(repository,revision,expected_path)
 if local_records!=1: raise Refusal("pinned NixKit sources.json must contain exactly the audited local watch record")
 if set(catalog)!=NIXKIT_SKILLS: raise Refusal("pinned NixKit sources.json does not contain the exact audited skill catalog")
 return catalog

def _canonical_manifest(manifest:dict)->None:
 if set(manifest)!={"schema_version","nixkit","packs","profiles","host_native_satisfaction","proposed_pack_additions","approved_pack_additions","approved_additions","task_only","excluded","governance"} or manifest.get("schema_version")!=1: raise Refusal("canonical pack manifest has invalid fields")
 if manifest.get("nixkit")!={"repository":NIXKIT_REPOSITORY,"commit":NIXKIT_COMMIT,"sources_sha256":NIXKIT_SOURCES_SHA256,"node_runtime":NODE_RUNTIME}: raise Refusal("canonical manifest does not match compile-time NixKit pins")
 if manifest.get("packs")!=EXPECTED_PACKS or manifest.get("profiles")!=EXPECTED_ASSIGNMENTS: raise Refusal("canonical manifest does not match the exact seven-profile pack topology")
 if manifest.get("host_native_satisfaction")!=EXPECTED_HOST_NATIVE_SATISFACTION: raise Refusal("canonical manifest does not match compile-time host-native satisfaction bindings")
 additions=manifest.get("approved_additions")
 if not isinstance(additions,dict) or set(additions)!=set(PROFILE_MAP) or any(not isinstance(v,list) or len(v)!=len(set(v)) or any(not isinstance(x,str) or not SKILL.fullmatch(x) for x in v) for v in additions.values()): raise Refusal("canonical manifest has invalid approved additions topology")
 proposed=manifest.get("proposed_pack_additions"); approved=manifest.get("approved_pack_additions")
 if not isinstance(proposed,dict) or not isinstance(approved,dict): raise Refusal("canonical manifest pack additions must be profile maps")
 for label,values in (("proposed",proposed),("approved",approved)):
  if set(values)!=set(PROFILE_MAP) or any(not isinstance(v,list) or len(v)!=len(set(v)) or any(not isinstance(x,str) or x not in EXPECTED_PACKS for x in v) for v in values.values()): raise Refusal(f"canonical manifest has invalid {label} pack additions topology")
 if any(set(proposed[p]) & set(approved[p]) for p in PROFILES): raise Refusal("proposed and approved pack additions must be disjoint")
 if set(proposed["lnx"]) | set(approved["lnx"])!={"product-development"} or any(proposed[p] or approved[p] for p in PROFILES-{"lnx"}): raise Refusal("product-development must be exactly one pending or approved LNX pack addition")
 if any(pack in manifest["profiles"][profile] for profile in PROFILES for pack in proposed[profile]+approved[profile]): raise Refusal("pack additions must not be folded into baseline profile assignments")
 persistent=set().union(*(set(v) for v in EXPECTED_PACKS.values())); task=manifest.get("task_only"); excluded=manifest.get("excluded")
 if not isinstance(task,list) or not isinstance(excluded,list) or len(task)!=len(set(task)) or len(excluded)!=len(set(excluded)) or persistent & set(task) or persistent & set(excluded) or set(task)&set(excluded) or persistent|set(task)|set(excluded)!=NIXKIT_SKILLS: raise Refusal("canonical manifest does not classify the exact NixKit catalog")
 governance=manifest.get("governance")
 if not isinstance(governance,dict) or set(governance.get("hard_gates",[]))!=HARD_GATES or governance.get("minimum_score")!=80 or governance.get("approval_authority")!="Lior": raise Refusal("canonical manifest governance is invalid")

def _tracked_clean(relative:Path)->None:
 _run(["git","ls-files","--error-unmatch",relative.as_posix()],ROOT)
 if _run(["git","status","--porcelain=v1","--untracked-files=all","--",relative.as_posix()],ROOT): raise Refusal(f"canonical governance file is dirty: {relative.as_posix()}")

def _git_json(commit:str,relative:Path,label:str)->dict:
 try: raw=_run(["git","show",f"{commit}:{relative.as_posix()}"],ROOT); value=json.loads(raw)
 except (Refusal,json.JSONDecodeError) as exc: raise Refusal(f"cannot load committed {label} at {commit}: {exc}") from exc
 if not isinstance(value,dict): raise Refusal(f"committed {label} must be an object")
 return value

def _git_bytes(commit:str,relative:Path,label:str)->bytes:
 result=subprocess.run(["git","show",f"{commit}:{relative.as_posix()}"],cwd=ROOT,capture_output=True,check=False)
 if result.returncode: raise Refusal(f"cannot load committed {label} at {commit}: {result.stderr.decode(errors='replace').strip() or result.returncode}")
 return result.stdout

def _valid_datetime(value:object)->bool:
 if not isinstance(value,str) or not RFC3339.fullmatch(value): return False
 try: parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
 except ValueError: return False
 return parsed.tzinfo is not None and parsed.utcoffset() is not None

def _valid_discord_evidence(value:object,request:bool)->bool:
 required={"kind","platform","author_id","channel_id","message_id","quote","timestamp"}
 if not isinstance(value,dict) or set(value)!=required or value.get("platform")!="discord" or value.get("kind")!=("request/proposal" if request else "approval"): return False
 if not all(isinstance(value.get(k),str) and re.fullmatch(r"[0-9]{17,20}",value[k]) for k in ("author_id","channel_id","message_id")): return False
 return isinstance(value.get("quote"),str) and 0<len(value["quote"])<=MAX_TEXT and _valid_datetime(value.get("timestamp"))

def _validate_pack_approval_evidence(value:object,proposal_id:str,proposal_git_commit:str)->None:
 if not _valid_discord_evidence(value,False): raise Refusal("pack approval evidence is malformed")
 assert isinstance(value,dict)
 if value["author_id"]!=APPROVAL_AUTHORITY_DISCORD_ID: raise Refusal("pack approval evidence author is not Lior's verified Discord user")
 if value["channel_id"]!=APPROVAL_CHANNEL_ID: raise Refusal("pack approval evidence is not from the proposal channel")
 expected_quote=f"approve {proposal_id} at {proposal_git_commit}"
 if value["quote"]!=expected_quote: raise Refusal(f"pack approval evidence quote must exactly equal {expected_quote!r}")
 if proposal_id==PACK_PROPOSALS[("lnx","product-development")] and (proposal_git_commit!=PRODUCT_PROPOSAL_COMMIT or value!=PRODUCT_APPROVAL_EVIDENCE): raise Refusal("product-development approval must match the exact verified Discord message ID, timestamp, and proposal commit")

def _validate_pack_proposal(p:dict,proposal_id:str,profile:str,pack:str,catalog:dict[str,tuple[str,str,str|None]])->None:
 required={"schema_version","proposal_id","profile","pack","ordered_skills","nixkit","request_evidence","status"}
 expected_nixkit=PRODUCT_NIXKIT if proposal_id==PACK_PROPOSALS[("lnx","product-development")] else {"repository":NIXKIT_REPOSITORY,"commit":NIXKIT_COMMIT,"snapshot_commit":NIXKIT_SNAPSHOT_COMMIT,"sources_sha256":NIXKIT_SOURCES_SHA256}
 if set(p)!=required or p.get("schema_version")!=1 or p.get("proposal_id")!=proposal_id or not PROPOSAL_ID.fullmatch(str(p.get("proposal_id",""))) or p.get("profile")!=profile or p.get("pack")!=pack or p.get("status")!="pending_approval": raise Refusal("committed pack proposal identity/status is invalid")
 skills=p.get("ordered_skills")
 if not isinstance(skills,list) or skills!=EXPECTED_PACKS[pack] or len(skills)!=len(set(skills)) or any(not isinstance(x,str) or not SKILL.fullmatch(x) for x in skills): raise Refusal("committed pack proposal ordered skills do not match the exact pack")
 if p.get("nixkit")!=expected_nixkit: raise Refusal("committed pack proposal does not bind the exact NixKit checkout, snapshot, and sources hash")
 if not _valid_discord_evidence(p.get("request_evidence"),True): raise Refusal("committed pack proposal request evidence is invalid")
 if proposal_id==PACK_PROPOSALS[("lnx","product-development")] and p["request_evidence"]!=PRODUCT_REQUEST_EVIDENCE: raise Refusal("product-development request/proposal evidence is not exact")
 if any(catalog.get(skill) is None or catalog[skill][:2]!=("its-nix/kit",NIXKIT_SNAPSHOT_COMMIT) for skill in PRODUCT_DEVELOPMENT_SKILLS[:7]): raise Refusal("product-development snapshot skills do not match the exact pinned source snapshot")

def _validate_declared_pack_proposals(manifest:dict,catalog:dict[str,tuple[str,str,str|None]])->None:
 expected_proposals={f"{PACK_PROPOSALS[(profile,pack)]}.json" for profile in PROFILES for pack in manifest["proposed_pack_additions"][profile]+manifest["approved_pack_additions"][profile]}
 proposal_root=ROOT/"skills/pack-proposals"
 if proposal_root.is_symlink() or not proposal_root.is_dir(): raise Refusal("skills/pack-proposals must be a real directory")
 try: proposal_entries=list(proposal_root.iterdir())
 except OSError as exc: raise Refusal(f"cannot enumerate skills/pack-proposals: {exc}") from exc
 if any(path.is_symlink() or not path.is_file() or path.suffix!=".json" for path in proposal_entries): raise Refusal("skills/pack-proposals contains an unsupported entry")
 actual_proposals={path.name for path in proposal_entries}
 if actual_proposals!=expected_proposals: raise Refusal("pack proposal files do not exactly match canonical manifest declarations")
 expected_receipts={f"{PACK_PROPOSALS[(profile,pack)]}.json" for profile in PROFILES for pack in manifest["approved_pack_additions"][profile]}
 approval_root=ROOT/"skills/pack-approvals"
 if approval_root.exists() and (approval_root.is_symlink() or not approval_root.is_dir()): raise Refusal("skills/pack-approvals must be a real directory")
 try: approval_entries=list(approval_root.iterdir()) if approval_root.is_dir() else []
 except OSError as exc: raise Refusal(f"cannot enumerate skills/pack-approvals: {exc}") from exc
 if any(path.is_symlink() or not path.is_file() or path.suffix!=".json" for path in approval_entries): raise Refusal("skills/pack-approvals contains an unsupported entry")
 actual_receipts={path.name for path in approval_entries}
 if actual_receipts!=expected_receipts: raise Refusal("skills/pack-approvals files do not exactly match approved pack declarations")
 for profile in PROFILES:
  for pack in manifest["proposed_pack_additions"][profile]+manifest["approved_pack_additions"][profile]:
   proposal_id=PACK_PROPOSALS.get((profile,pack))
   if proposal_id is None: raise Refusal("pack addition has no canonical proposal binding")
   relative=Path("skills/pack-proposals")/f"{proposal_id}.json"; _tracked_clean(relative)
   _validate_pack_proposal(_load_json(ROOT/relative,"pack proposal"),proposal_id,profile,pack,catalog)

def _pack_approval(profile:str,pack:str,current:dict,catalog:dict[str,tuple[str,str,str|None]])->None:
 proposal_id=PACK_PROPOSALS.get((profile,pack))
 if proposal_id is None: raise Refusal("approved pack has no canonical proposal binding")
 relative=Path("skills/pack-approvals")/f"{proposal_id}.json"; _tracked_clean(relative)
 receipt=_load_json(ROOT/relative,"pack approval receipt")
 required={"schema_version","proposal_id","proposal_git_commit","proposal_file_sha256","profile","pack","ordered_skills","nixkit","approved_by","approval_evidence"}
 expected_nixkit=PRODUCT_NIXKIT if proposal_id==PACK_PROPOSALS[("lnx","product-development")] else {"repository":NIXKIT_REPOSITORY,"commit":NIXKIT_COMMIT,"snapshot_commit":NIXKIT_SNAPSHOT_COMMIT,"sources_sha256":NIXKIT_SOURCES_SHA256}
 if set(receipt)!=required or receipt.get("schema_version")!=1 or receipt.get("proposal_id")!=proposal_id or receipt.get("profile")!=profile or receipt.get("pack")!=pack or receipt.get("ordered_skills")!=EXPECTED_PACKS[pack] or receipt.get("nixkit")!=expected_nixkit or receipt.get("approved_by")!="Lior": raise Refusal("malformed, forged, or misbound pack approval receipt")
 commit=receipt.get("proposal_git_commit"); digest=receipt.get("proposal_file_sha256")
 if not isinstance(commit,str) or not SHA.fullmatch(commit) or not isinstance(digest,str) or not re.fullmatch(r"[a-f0-9]{64}",digest): raise Refusal("pack approval proposal commit/hash is invalid")
 if proposal_id==PACK_PROPOSALS[("lnx","product-development")] and (commit!=PRODUCT_PROPOSAL_COMMIT or digest!=PRODUCT_PROPOSAL_SHA256): raise Refusal("product-development approval does not bind the exact canonical proposal commit and file hash")
 evidence=receipt.get("approval_evidence"); _validate_pack_approval_evidence(evidence,proposal_id,commit)
 assert isinstance(evidence,dict)
 if (evidence["channel_id"],evidence["message_id"])==(PRODUCT_REQUEST_EVIDENCE["channel_id"],PRODUCT_REQUEST_EVIDENCE["message_id"]) or datetime.fromisoformat(evidence["timestamp"].replace("Z","+00:00"))<=datetime.fromisoformat(PRODUCT_REQUEST_EVIDENCE["timestamp"].replace("Z","+00:00")): raise Refusal("pack approval evidence must be distinct from and later than request/proposal evidence")
 _run(["git","merge-base","--is-ancestor",commit,"HEAD"],ROOT)
 proposal_path=Path("skills/pack-proposals")/f"{proposal_id}.json"; raw=_git_bytes(commit,proposal_path,"pack proposal")
 if hashlib.sha256(raw).hexdigest()!=digest: raise Refusal("pack approval proposal file SHA-256 does not match committed bytes")
 try: proposal=json.loads(raw)
 except json.JSONDecodeError as exc: raise Refusal(f"committed pack proposal is invalid JSON: {exc}") from exc
 if not isinstance(proposal,dict): raise Refusal("committed pack proposal must be an object")
 _validate_pack_proposal(proposal,proposal_id,profile,pack,catalog)
 committed_manifest=_git_json(commit,Path("skills/packs.json"),"manifest")
 if proposal_id==PACK_PROPOSALS[("lnx","product-development")]:
  if hashlib.sha256(_git_bytes(commit,Path("skills/packs.json"),"product proposal manifest")).hexdigest()!=PRODUCT_PROPOSAL_MANIFEST_SHA256: raise Refusal("product-development proposal manifest no longer matches its exact reviewed bytes")
 else: _canonical_manifest(committed_manifest)
 if pack not in committed_manifest["proposed_pack_additions"][profile] or pack in committed_manifest["approved_pack_additions"][profile]: raise Refusal("proposal commit must contain the pending, unapproved pack state")
 if pack not in current["approved_pack_additions"][profile] or pack in current["proposed_pack_additions"][profile]: raise Refusal("current manifest does not contain the exact approved pack state")

def _validate_proposal(p:dict,proposal_id:str,profile:str,skills:set[str],catalog:dict[str,tuple[str,str,str|None]])->None:
 required={"schema_version","proposal_id","profile","name","summary","rationale","source_url","repository","skill_path","revision","license","evidence_urls","risks","score_components","score","gates","gate_evidence","status"}
 if set(p)!=required or p.get("schema_version")!=1 or not isinstance(p.get("proposal_id"),str) or not PROPOSAL_ID.fullmatch(p["proposal_id"]) or p.get("proposal_id")!=proposal_id or p.get("profile") not in PROFILES or p.get("profile")!=profile or p.get("status")!="candidate" or not isinstance(p.get("name"),str) or not SKILL.fullmatch(p["name"]) or {p["name"]}!=skills: raise Refusal("committed proposal identity/status/skills do not match approval")
 if not isinstance(p.get("repository"),str) or not REPOSITORY.fullmatch(p["repository"]) or not isinstance(p.get("skill_path"),str) or not SKILL_PATH.fullmatch(p["skill_path"]) or not SHA.fullmatch(str(p.get("revision",""))): raise Refusal("committed proposal repository/path/revision is invalid")
 identity=catalog.get(p["name"])
 historical_watch=(proposal_id=="lnx-watch-a9cb5b57" and p["name"]=="watch" and p["repository"]=="its-nix/kit" and p["revision"]=="a9cb5b576097301ee63485649d5703db7479ad8f" and identity==("its-nix/kit",NIXKIT_COMMIT,"skills/watch"))
 if identity is None or (identity[:2]!=(p["repository"],p["revision"]) and not historical_watch): raise Refusal("approved skill does not uniquely belong to the proposal repository and pinned revision")
 if identity[2] is not None and p["skill_path"]!=identity[2]: raise Refusal("approved local skill does not match its exact audited repository path")
 source=f"https://github.com/{p['repository']}/tree/{p['revision']}/{p.get('skill_path','')}"
 if not isinstance(p.get("source_url"),str) or len(p["source_url"])>MAX_URL or p["source_url"]!=source: raise Refusal("committed proposal source evidence is not immutable and exact")
 license_prefixes=(f"/{p['repository']}/blob/{p['revision']}/",f"/{p['repository']}/tree/{p['revision']}/")
 def license_url(u:object)->bool:
  if not isinstance(u,str): return False
  parsed=urlsplit(u)
  if parsed.scheme!="https" or parsed.netloc!="github.com" or parsed.username is not None or parsed.password is not None or parsed.port is not None or parsed.query or parsed.fragment: return False
  if not any(parsed.path.startswith(prefix) for prefix in license_prefixes): return False
  relative=next(parsed.path[len(prefix):] for prefix in license_prefixes if parsed.path.startswith(prefix))
  return any(re.fullmatch(r"(?:LICENSE|COPYING|NOTICE)(?:[._-].*)?",part,re.I) for part in relative.split("/") if part)
 evidence=p.get("evidence_urls")
 if not isinstance(evidence,list) or not 2<=len(evidence)<=MAX_EVIDENCE_URLS or any(not isinstance(u,str) or not u.strip() or len(u)>MAX_URL or urlsplit(u).scheme!="https" or not urlsplit(u).netloc or urlsplit(u).username is not None or urlsplit(u).password is not None for u in evidence) or source not in evidence or not any(license_url(u) for u in evidence): raise Refusal("committed proposal lacks immutable same-origin source/license evidence")
 risks=p.get("risks")
 if not isinstance(risks,list) or len(risks)>MAX_RISKS or any(not isinstance(risk,str) or not risk.strip() or len(risk)>MAX_TEXT for risk in risks): raise Refusal("committed proposal risks are invalid")
 components=p.get("score_components"); score=p.get("score")
 if not isinstance(components,dict) or set(components)!=set(SCORE_LIMITS) or any(isinstance(components[k],bool) or not isinstance(components[k],int) or not 0<=components[k]<=SCORE_LIMITS[k] for k in SCORE_LIMITS) or isinstance(score,bool) or not isinstance(score,int) or not 80<=score<=100 or score!=sum(components.values()): raise Refusal("committed proposal score is invalid")
 if p.get("gates")!={k:True for k in HARD_GATES} or not isinstance(p.get("gate_evidence"),dict) or set(p["gate_evidence"])!=HARD_GATES or any(not isinstance(v,str) or not v.strip() or len(v)>MAX_TEXT for v in p["gate_evidence"].values()): raise Refusal("committed proposal gates/evidence are invalid")
 for field in ("summary","rationale","license"):
  if not isinstance(p.get(field),str) or not p[field].strip() or len(p[field])>MAX_TEXT: raise Refusal(f"committed proposal {field} is invalid")

def _approval(path:Path,profile:str,current:dict,requested:set[str],catalog:dict[str,tuple[str,str,str|None]])->set[str]:
 for governance_file in (Path("skills/packs.json"), Path("scripts/install-skills.py")): _tracked_clean(governance_file)
 try: relative=path.resolve(strict=True).relative_to(ROOT.resolve())
 except (OSError,ValueError) as exc: raise Refusal("approval receipt must be a canonical path inside this repository") from exc
 _tracked_clean(relative)
 approval=_load_json(path,"approval receipt"); required={"schema_version","proposal_id","profile","skills","approved_by","approved_at","manifest_commit"}
 if set(approval)!=required or approval.get("schema_version")!=1 or approval.get("profile")!=profile or approval.get("approved_by")!="Lior": raise Refusal("malformed or misbound approval receipt")
 proposal_id=approval.get("proposal_id")
 if not isinstance(proposal_id,str) or not PROPOSAL_ID.fullmatch(proposal_id) or relative!=Path("skills/approvals")/f"{proposal_id}.json": raise Refusal("approval receipt is not at its canonical proposal-bound path")
 skills=approval.get("skills"); commit=approval.get("manifest_commit")
 approved=set(skills) if isinstance(skills,list) else set()
 if not isinstance(skills,list) or not approved<=requested or len(skills)!=len(approved) or not skills or any(not isinstance(x,str) or not SKILL.fullmatch(x) for x in skills): raise Refusal("approval receipt skills must be a nonempty exact subset of requested additions")
 if not isinstance(commit,str) or not SHA.fullmatch(commit) or not _valid_datetime(approval.get("approved_at")): raise Refusal("approval receipt commit/time is invalid")
 _run(["git","merge-base","--is-ancestor",commit,"HEAD"],ROOT)
 committed_manifest=_git_json(commit,Path("skills/packs.json"),"manifest")
 if commit in HISTORICAL_APPROVAL_MANIFESTS:
  expected_hash,expected_additions=HISTORICAL_APPROVAL_MANIFESTS[commit]
  historical_hash=hashlib.sha256(_git_bytes(commit,Path("skills/packs.json"),"historical approval manifest")).hexdigest()
  if historical_hash!=expected_hash or committed_manifest.get("profiles")!=EXPECTED_ASSIGNMENTS or committed_manifest.get("approved_additions",{}).get("lnx")!=expected_additions: raise Refusal("historical approval manifest no longer matches its exact reviewed bytes and state")
 else: _canonical_manifest(committed_manifest)
 proposal_path=Path("skills/proposals")/f"{proposal_id}.json"; _tracked_clean(proposal_path)
 proposal=_git_json(commit,proposal_path,"proposal"); _validate_proposal(proposal,proposal_id,profile,approved,catalog)
 if committed_manifest["profiles"][profile]!=current["profiles"][profile] or not approved<=set(committed_manifest["approved_additions"][profile]) or not approved<=set(current["approved_additions"][profile]): raise Refusal("approved scope is absent from the committed or current profile assignment/additions")
 return approved

def _check_no_symlink(path:Path)->None:
 current=path
 while True:
  if current.is_symlink(): raise Refusal(f"profile home path contains a symlink: {current}")
  if current==current.parent: break
  current=current.parent

def _frontmatter_name(path:Path)->str:
 try: text=path.read_text(encoding="utf-8")
 except (OSError,UnicodeError) as exc: raise Refusal(f"cannot read installed skill frontmatter {path}: {exc}") from exc
 lines=text.splitlines()
 if not lines or lines[0].strip()!="---": raise Refusal(f"installed skill has no validated YAML frontmatter: {path}")
 try: end=next(i for i,line in enumerate(lines[1:201],1) if line.strip()=="---")
 except StopIteration as exc: raise Refusal(f"installed skill frontmatter is unterminated: {path}") from exc
 matches=[]
 for line in lines[1:end]:
  match=re.fullmatch(r"\s*name\s*:\s*(['\"]?)([a-z0-9]+(?:-[a-z0-9]+)*)\1\s*(?:#.*)?",line)
  if match: matches.append(match.group(2))
 if len(matches)!=1 or not SKILL.fullmatch(matches[0]): raise Refusal(f"installed skill frontmatter must contain exactly one validated name: {path}")
 return matches[0]

def _skill_tree_digest(root:Path)->str:
 """Hash one non-symlink tree in per-directory byte order, depth first."""
 if not HOST_NATIVE_DESCRIPTOR_TRAVERSAL:
  raise Refusal("descriptor-relative non-following host-native traversal is unavailable")
 digest=hashlib.sha256(b"hermes-host-native-skill-tree-v1\0")
 seen:set[str]=set()
 def identity(info:os.stat_result)->tuple[int,int,int,int,int]:
  return info.st_dev,info.st_ino,stat.S_IFMT(info.st_mode),stat.S_IMODE(info.st_mode),info.st_size
 def changed(display:Path)->Refusal:
  return Refusal(f"host-native skill entry changed while being hashed: {display}")
 def add(relative:str,kind:bytes,mode:int,size:int=0)->None:
  if relative in seen: raise Refusal(f"host-native skill tree contains a duplicate path: {relative}")
  seen.add(relative)
  try: encoded=relative.encode("utf-8","strict")
  except UnicodeEncodeError as exc: raise Refusal(f"host-native skill tree path is not UTF-8: {relative!r}") from exc
  if relative!="." and (relative.startswith("/") or any(part in {"",".",".."} for part in relative.split("/"))): raise Refusal(f"host-native skill tree has an invalid relative path: {relative}")
  digest.update(kind); digest.update(mode.to_bytes(2,"big")); digest.update(len(encoded).to_bytes(8,"big")); digest.update(encoded)
  if kind==b"f": digest.update(size.to_bytes(8,"big"))
 try: root_info=root.lstat()
 except OSError as exc: raise Refusal(f"cannot inspect host-native skill tree {root}: {exc}") from exc
 if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode): raise Refusal(f"host-native skill tree must be a real directory: {root}")
 root_fd=-1
 try:
  root_fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  opened_root=os.fstat(root_fd)
  if identity(opened_root)!=identity(root_info) or not stat.S_ISDIR(opened_root.st_mode): raise changed(root)
  add(".",b"d",stat.S_IMODE(opened_root.st_mode))
  def walk(directory_fd:int,directory:Path,prefix:str="")->None:
   try:
    with os.scandir(directory_fd) as scanned: entries=sorted(scanned,key=lambda entry:os.fsencode(entry.name))
   except OSError as exc: raise Refusal(f"cannot enumerate host-native skill tree {directory}: {exc}") from exc
   for entry in entries:
    relative=f"{prefix}/{entry.name}" if prefix else entry.name; display=directory/entry.name
    try: before=entry.stat(follow_symlinks=False)
    except OSError as exc: raise Refusal(f"cannot inspect host-native skill entry {display}: {exc}") from exc
    mode=stat.S_IMODE(before.st_mode)
    if stat.S_ISDIR(before.st_mode):
     child_fd=-1
     try:
      child_fd=os.open(entry.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory_fd)
      opened=os.fstat(child_fd)
      if identity(opened)!=identity(before) or not stat.S_ISDIR(opened.st_mode): raise changed(display)
      add(relative,b"d",mode); walk(child_fd,display,relative)
      after=os.stat(entry.name,dir_fd=directory_fd,follow_symlinks=False)
      if identity(os.fstat(child_fd))!=identity(opened) or identity(after)!=identity(opened): raise changed(display)
     except Refusal: raise
     except OSError as exc: raise Refusal(f"cannot traverse host-native skill directory {display}: {exc}") from exc
     finally:
      if child_fd>=0: os.close(child_fd)
    elif stat.S_ISREG(before.st_mode):
     descriptor=-1
     try:
      descriptor=os.open(entry.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory_fd)
      opened=os.fstat(descriptor)
      if identity(opened)!=identity(before) or not stat.S_ISREG(opened.st_mode): raise changed(display)
      add(relative,b"f",mode,opened.st_size)
      remaining=opened.st_size
      while remaining:
       chunk=os.read(descriptor,min(1024*1024,remaining))
       if not chunk: raise changed(display)
       digest.update(chunk); remaining-=len(chunk)
      if os.read(descriptor,1): raise changed(display)
      after=os.stat(entry.name,dir_fd=directory_fd,follow_symlinks=False)
      if identity(os.fstat(descriptor))!=identity(opened) or identity(after)!=identity(opened): raise changed(display)
     except Refusal: raise
     except OSError as exc: raise Refusal(f"cannot hash host-native skill file {display}: {exc}") from exc
     finally:
      if descriptor>=0: os.close(descriptor)
    else: raise Refusal(f"host-native skill tree contains a symlink or special entry: {display}")
  walk(root_fd,root)
  if identity(os.fstat(root_fd))!=identity(opened_root) or identity(root.lstat())!=identity(opened_root): raise changed(root)
 except Refusal: raise
 except OSError as exc: raise Refusal(f"cannot hash host-native skill tree {root}: {exc}") from exc
 finally:
  if root_fd>=0: os.close(root_fd)
 return digest.hexdigest()

def _validate_host_native_authorization(value:dict,declarations:dict)->None:
 if value!=EXPECTED_HOST_NATIVE_AUTHORIZATION: raise Refusal("host-native preservation authorization is not the exact compile-time record")
 if value["artifacts"]!=declarations.get("lnx"): raise Refusal("host-native preservation authorization does not exactly bind the manifest artifacts")

def _validate_host_native_satisfaction(profile:str,profile_home:Path,declarations:dict)->dict[str,Path]:
 records=declarations.get(profile)
 if not isinstance(records,list): raise Refusal("host-native satisfaction declarations must be exact profile lists")
 satisfied:dict[str,Path]={}; used_paths:set[str]=set()
 for record in records:
  if not isinstance(record,dict) or set(record)!={"skill","path","tree_sha256"}: raise Refusal("host-native satisfaction record has invalid fields")
  skill=record.get("skill"); relative=record.get("path"); expected=record.get("tree_sha256")
  if not isinstance(skill,str) or not SKILL.fullmatch(skill) or not isinstance(relative,str) or not SKILL_PATH.fullmatch(relative) or not isinstance(expected,str) or not re.fullmatch(r"[a-f0-9]{64}",expected): raise Refusal("host-native satisfaction record is malformed")
  if skill in satisfied or relative in used_paths: raise Refusal("host-native satisfaction declarations contain duplicate skills or paths")
  root=profile_home/"skills"/relative
  _check_no_symlink(root)
  if _skill_tree_digest(root)!=expected: raise Refusal(f"host-native skill tree digest mismatch: {skill} at {relative}")
  manifest_path=root/"SKILL.md"
  if _frontmatter_name(manifest_path)!=skill: raise Refusal(f"host-native skill frontmatter name does not match declaration: {skill}")
  if _skill_tree_digest(root)!=expected: raise Refusal(f"host-native skill tree changed during validation: {skill} at {relative}")
  satisfied[skill]=manifest_path; used_paths.add(relative)
 return satisfied

def _preflight_duplicate_names(profile_home:Path,selected:set[str],host_native:dict[str,Path]|None=None)->None:
 skill_root=profile_home/"skills"
 if skill_root.is_symlink(): raise Refusal("profile skills path must be a real non-symlink directory")
 if not skill_root.exists(): return
 if not skill_root.is_dir(): raise Refusal("profile skills path must be a real non-symlink directory")
 inventory:dict[str,list[Path]]={}
 paths:list[Path]=[]; pending=[skill_root]
 while pending:
  directory=pending.pop()
  try: entries=sorted(directory.iterdir())
  except OSError as exc: raise Refusal(f"cannot inventory profile skills recursively: {exc}") from exc
  for entry in entries:
   if entry.is_symlink(): raise Refusal(f"profile skills tree must not contain symlinks: {entry}")
   if entry.is_dir(): pending.append(entry)
   elif entry.name=="SKILL.md": paths.append(entry)
 for path in paths:
  if not path.is_file(): raise Refusal(f"installed skill manifest must be a regular non-symlink file: {path}")
  name=_frontmatter_name(path); inventory.setdefault(name,[]).append(path)
 for name in sorted(selected):
  found=inventory.get(name,[]); canonical=skill_root/name/"SKILL.md"
  if len(found)>1 or any(path!=canonical for path in found):
   locations=", ".join(str(path) for path in found)
   raise Refusal(f"selected skill name {name} already exists outside its sole canonical destination: {locations}")
 for name,expected in sorted((host_native or {}).items()):
  found=inventory.get(name,[])
  if found!=[expected]:
   locations=", ".join(str(path) for path in found)
   raise Refusal(f"host-native skill name {name} must exist exactly once at its declared path: {locations}")

def main()->int:
 parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--profile",required=True,choices=sorted(PROFILE_MAP)); parser.add_argument("--nixkit",type=Path,default=Path.home()/"src/nixkit"); parser.add_argument("--skill",action="append",default=[]); parser.add_argument("--approval",type=Path,action="append",default=[]); parser.add_argument("--apply",action="store_true"); args=parser.parse_args()
 try:
  manifest=_load_json(CANONICAL_MANIFEST,"canonical pack manifest"); _canonical_manifest(manifest)
  host_native_authorization=_load_json(HOST_NATIVE_AUTHORIZATION,"host-native preservation authorization"); _validate_host_native_authorization(host_native_authorization,manifest["host_native_satisfaction"])
  checkout=args.nixkit.expanduser().absolute()
  if checkout.is_symlink() or not checkout.is_dir(): raise Refusal("NixKit checkout must be an existing non-symlink directory")
  if _run(["git","rev-parse","HEAD"],checkout)!=NIXKIT_COMMIT: raise Refusal(f"NixKit HEAD does not match pinned commit {NIXKIT_COMMIT}")
  if _run(["git","status","--porcelain","--untracked-files=all"],checkout): raise Refusal("NixKit checkout must be clean, including untracked files")
  sources=checkout/"sources.json"
  try: actual_hash=hashlib.sha256(sources.read_bytes()).hexdigest()
  except OSError as exc: raise Refusal(f"cannot read pinned NixKit sources.json: {exc}") from exc
  if actual_hash!=NIXKIT_SOURCES_SHA256: raise Refusal("NixKit sources.json content does not match the audited hash")
  catalog=_source_catalog(sources)
  local_paths={identity[2] for identity in catalog.values() if identity[2] in NIXKIT_LOCAL_TREES}
  if local_paths!=set(NIXKIT_LOCAL_TREES): raise Refusal("pinned NixKit local source paths do not match the audited tree set")
  for relative,tree in NIXKIT_LOCAL_TREES.items(): _verify_local_tree(checkout,relative,tree)
  _validate_declared_pack_proposals(manifest,catalog)
  for approved_profile in PROFILES:
   for approved_pack in manifest["approved_pack_additions"][approved_profile]: _pack_approval(approved_profile,approved_pack,manifest,catalog)
  installer=_verify_installer(checkout)
  npx=shutil.which("npx")
  if npx is None or not os.access(npx,os.X_OK): raise Refusal("executable npx is required for the pinned Node runtime")
  effective_packs=manifest["profiles"][args.profile]+manifest["approved_pack_additions"][args.profile]
  baseline={skill for pack in effective_packs for skill in manifest["packs"][pack]}; additions=set(args.skill)
  if len(args.skill)!=len(additions) or any(not SKILL.fullmatch(s) for s in additions): raise Refusal("invalid or duplicate requested skill name")
  declared=set(manifest["approved_additions"][args.profile])
  if additions:
   if additions!=declared or not args.approval: raise Refusal("requested additions require exact canonical declaration and approval coverage")
   covered:set[str]=set()
   for approval_path in args.approval:
    approved=_approval(approval_path.expanduser().absolute(),args.profile,manifest,additions,catalog)
    if covered&approved: raise Refusal("approval receipt scopes must be disjoint")
    covered|=approved
   if covered!=additions: raise Refusal("approval receipts do not exactly cover requested additions")
  elif args.approval or declared: raise Refusal("declared additions require matching --skill values and approval")
  if args.apply:
   if _run(["git","status","--porcelain","--untracked-files=all"],ROOT): raise Refusal("Nix OS repository must be clean, including untracked files, for apply")
   for relative in (Path("skills/packs.json"),Path("scripts/install-skills.py"),Path("skills/host-native-approvals/lnx-preserve-host-native-v1.json")): _tracked_clean(relative)
  hermes_home=Path(os.environ.get("HERMES_HOME",str(Path.home()/".hermes"))).expanduser().absolute(); profile_home=hermes_home if PROFILE_MAP[args.profile]=="default" else hermes_home/"profiles"/PROFILE_MAP[args.profile]; _check_no_symlink(profile_home)
  host_native=_validate_host_native_satisfaction(args.profile,profile_home,manifest["host_native_satisfaction"])
  effective=baseline|additions
  if not set(host_native)<=effective: raise Refusal("host-native satisfaction may cover only effective profile skills")
  install_targets=effective-set(host_native)
  _preflight_duplicate_names(profile_home,install_targets,host_native)
  command=[npx,"--yes",f"node@{NODE_RUNTIME}",str(installer),"--apply","--skills","--agent","hermes","--prefix",str(profile_home)]
  for skill in sorted(install_targets): command.extend(["--skill",skill])
  print(("APPLY" if args.apply else "PREVIEW")+f" profile={args.profile} home={profile_home}")
  if host_native: print("host-native:"," ".join(sorted(host_native)))
  print("command:"," ".join(json.dumps(v) for v in command))
  if not args.apply:return 0
  _verify_installer(checkout)
  for relative,tree in NIXKIT_LOCAL_TREES.items(): _verify_local_tree(checkout,relative,tree)
  _preflight_duplicate_names(profile_home,install_targets,host_native)
  profile_home.mkdir(parents=True,exist_ok=True)
  if _validate_host_native_satisfaction(args.profile,profile_home,manifest["host_native_satisfaction"])!=host_native: raise Refusal("host-native satisfaction changed immediately before installation")
  result=subprocess.run(command,cwd=checkout,check=False)
  if _validate_host_native_satisfaction(args.profile,profile_home,manifest["host_native_satisfaction"])!=host_native: raise Refusal("host-native satisfaction changed during installation")
  if result.returncode: raise Refusal(f"NixKit refused installation (exit {result.returncode}); existing conflicts were preserved")
  print(f"Installed or preserved {len(install_targets)} NixKit skill(s) and validated {len(host_native)} host-native skill(s) for {args.profile}."); return 0
 except Refusal as exc: print(f"Refusing: {exc}",file=sys.stderr); return 2

if __name__=="__main__": raise SystemExit(main())
