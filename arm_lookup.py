#!/usr/bin/env python3
"""
ARM Metadata Quick Lookup CLI
Provides instant sub-millisecond lookups into the ARM Metadata Catalog.
"""

import os
import sys
import json
import sqlite3
import argparse

def get_db_path():
    # Priority path: user-specified storage location
    custom_location = r"C:\Work-Applications\ARM\MM_sqlite"
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    candidates = [
        os.path.join(custom_location, ".arm-catalog.sqlite"),
        os.path.join(custom_location, "arm-catalog.sqlite"),
        os.path.join(repo_root, ".arm-catalog.sqlite"),
        os.path.join(os.getcwd(), ".arm-catalog.sqlite"),
        ".arm-catalog.sqlite"
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

def search_entities(query, limit=20):
    db_path = get_db_path()
    if not db_path:
        print("Error: Catalog database (.arm-catalog.sqlite) not found. Run build_catalog.py first.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    print(f"\n--- Searching for: '{query}' ---")
    cur.execute("""
        SELECT id, type, name, file_name, file_path 
        FROM entities 
        WHERE id = ? OR name LIKE ? OR file_name LIKE ?
        LIMIT ?
    """, (query, f"%{query}%", f"%{query}%", limit))

    rows = cur.fetchall()
    if not rows:
        # Also check subtypes
        cur.execute("""
            SELECT parent_id, parent_name, subtype_id, subtype_name, manufacturer
            FROM subtypes
            WHERE subtype_id = ? OR subtype_name LIKE ?
            LIMIT ?
        """, (query, f"%{query}%", limit))
        sub_rows = cur.fetchall()
        if sub_rows:
            print(f"Found {len(sub_rows)} matching Subtypes:")
            for p_id, p_name, s_id, s_name, mfr in sub_rows:
                print(f"  [Subtype] {s_name} (ID: {s_id}, Mfr: {mfr}) -> Parent Entity: {p_name} ({p_id})")
        else:
            print("No matching entities or subtypes found.")
        conn.close()
        return

    print(f"Found {len(rows)} matching Entities:")
    for eid, etype, name, fname, fpath in rows:
        print(f"  [{etype}] {name} (ID: {eid})")
        print(f"       File: {fpath}")

    conn.close()

def find_container_for(entity_name_or_id):
    db_path = get_db_path()
    if not db_path:
        print("Error: Catalog database not found.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    print(f"\n--- Checking containers / parent shelves for: '{entity_name_or_id}' ---")
    
    # 1. Check direct containment relationships
    cur.execute("""
        SELECT source_name, relation_type, target_id, target_name, target_type
        FROM relationships
        WHERE (source_id = ? OR source_name LIKE ?) AND relation_type LIKE '%CONTAINER%'
    """, (entity_name_or_id, f"%{entity_name_or_id}%"))
    cont_rows = cur.fetchall()

    if cont_rows:
        print("Direct Container Allowances:")
        for s_name, rel, t_id, t_name, t_type in cont_rows:
            print(f"  '{s_name}' is allowed in Container: {t_name} (ID: {t_id})")

    # 2. Check if this is a shelf type embedded in a device/container subtype
    cur.execute("""
        SELECT source_name, target_name, target_type
        FROM relationships
        WHERE (target_name LIKE ? OR target_id = ?) AND relation_type = 'CONTAINS_SHELF'
    """, (f"%{entity_name_or_id}%", entity_name_or_id))
    shelf_rows = cur.fetchall()

    if shelf_rows:
        print("\nEntities holding this shelf type:")
        for s_name, t_name, t_type in shelf_rows:
            print(f"  Entity '{s_name}' mounts Shelf: {t_name}")

    if not cont_rows and not shelf_rows:
        print("No containment relationships found for this query.")

    conn.close()

def inspect_entity(entity_id_or_name):
    db_path = get_db_path()
    if not db_path:
        print("Error: Catalog database not found.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, type, name, file_name, file_path, data_json 
        FROM entities 
        WHERE id = ? OR name = ?
        LIMIT 1
    """, (entity_id_or_name, entity_id_or_name))
    row = cur.fetchone()

    if not row:
        # Fallback to like
        cur.execute("""
            SELECT id, type, name, file_name, file_path, data_json 
            FROM entities 
            WHERE name LIKE ?
            LIMIT 1
        """, (f"%{entity_id_or_name}%",))
        row = cur.fetchone()

    if not row:
        print(f"Entity '{entity_id_or_name}' not found.")
        conn.close()
        return

    eid, etype, name, fname, fpath, raw_json = row
    data = json.loads(raw_json)

    print(f"\n=======================================================")
    print(f"Entity: {name} (ID: {eid})")
    print(f"Type:   {etype}")
    print(f"File:   {fpath}")
    print(f"=======================================================")

    subtypes = data.get("subtypes", [])
    if subtypes:
        print(f"\nSubtypes ({len(subtypes)} total):")
        for st in subtypes[:10]:
            dims = f" [{st['dimensions']}]" if st.get("dimensions") else ""
            print(f"  - {st['name']} (ID: {st['id']}){dims}")
            for sh in st.get("shelfTypes", []):
                print(f"      -> Mounts Shelf: {sh['name']} (ID: {sh['id']})")
        if len(subtypes) > 10:
            print(f"  ... and {len(subtypes) - 10} more subtypes.")

    # Relationships
    cur.execute("""
        SELECT relation_type, target_name, target_id, target_type
        FROM relationships
        WHERE source_id = ?
    """, (eid,))
    rels = cur.fetchall()
    if rels:
        print("\nRelationships:")
        for r_type, t_name, t_id, t_type in rels:
            print(f"  - [{r_type}] -> {t_name} (ID: {t_id})")

    conn.close()

def main():
    parser = argparse.ArgumentParser(description="ARM Metadata Catalog Quick Lookup CLI")
    subparsers = parser.add_subparsers(dest="command", help="Lookup commands")

    # Search
    search_p = subparsers.add_parser("search", help="Search entities or subtypes by keyword or ID")
    search_p.add_argument("query", help="Keyword or ID to search")
    search_p.add_argument("--limit", type=int, default=15, help="Result limit")

    # Container-for
    cont_p = subparsers.add_parser("container-for", help="Find container or parent shelf for a given entity")
    cont_p.add_argument("entity", help="Entity name or ID")

    # Inspect
    inspect_p = subparsers.add_parser("inspect", help="Detailed inspection of an entity")
    inspect_p.add_argument("entity", help="Entity ID or exact Name")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "search":
        search_entities(args.query, args.limit)
    elif args.command == "container-for":
        find_container_for(args.entity)
    elif args.command == "inspect":
        inspect_entity(args.entity)

if __name__ == "__main__":
    main()
