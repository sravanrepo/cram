#!/usr/bin/env python
"""
ARM Device & Subtype Diagnostic Tool (arm_device_triage.py)
-----------------------------------------------------------
High-speed automated triage for ARM devices, subtypes, ports, slots,
naming templates, and TMF API transactions.

Usage:
  py arm_device_triage.py --node LON-CA-001828 [--env SIT2]
  py arm_device_triage.py --subtype "ACN METRO NID GT CHASSIS 1x AC" [--env SIT2]
  py arm_device_triage.py --audit "MetroNID" [--env SIT2]
  py arm_device_triage.py --log 123456 [--env SIT2]
"""

import sys
import argparse
import oracledb

ENV_CONFIGS = {
    'SIT2': {'dsn': 'azimtdb102:1521/AZT02ARM', 'user': 'CRAMER', 'pass': 'A2309_Inventory'},
    'TEST2': {'dsn': 'azimtdb102:1521/AZT02ARM', 'user': 'CRAMER', 'pass': 'A2309_Inventory'},
    'DEV2': {'dsn': 'azimddb102:1521/AZD02ARM', 'user': 'CRAMER', 'pass': 'A2309_Inventory'},
}

def get_connection(env_name, custom_dsn=None, custom_user=None, custom_pass=None):
    cfg = ENV_CONFIGS.get(env_name.upper(), ENV_CONFIGS['SIT2'])
    dsn = custom_dsn or cfg['dsn']
    user = custom_user or cfg['user']
    passwd = custom_pass or cfg['pass']
    return oracledb.connect(user=user, password=passwd, dsn=dsn)

def triage_node(cur, node_identifier):
    print(f"\n{'='*75}")
    print(f"TRIAGE FOR DEVICE: {node_identifier}")
    print(f"{'='*75}")

    # 1. Look up Node
    if str(node_identifier).isdigit():
        cur.execute("SELECT NODEID, NAME, FULLNAME, NODE2NODEDEF, NODE2LOCATION FROM NODE_O WHERE NODEID = :1", (int(node_identifier),))
    else:
        cur.execute("SELECT NODEID, NAME, FULLNAME, NODE2NODEDEF, NODE2LOCATION FROM NODE_O WHERE UPPER(NAME) = UPPER(:1)", (node_identifier.strip(),))
    row = cur.fetchone()
    if not row:
        print(f"[X] Device '{node_identifier}' NOT FOUND in NODE_O.")
        return

    node_id, node_name, full_name, nodedef_id, loc_id = row
    print(f"Device ID   : {node_id}")
    print(f"Device Name : {node_name} (Full: {full_name})")
    print(f"Location ID : {loc_id}")

    # 2. Subtype Details
    cur.execute("SELECT NODEDEFID, NAME FROM NODEDEF_M WHERE NODEDEFID = :1", (nodedef_id,))
    nd_row = cur.fetchone()
    subtype_name = nd_row[1] if nd_row else f"UNKNOWN ({nodedef_id})"
    print(f"Subtype     : {subtype_name} (ID: {nodedef_id})")

    # 3. Shelves
    cur.execute("SELECT SHELFID, NAME, SHELFNUMBER, SHELF2SHELFTYPE FROM SHELF_O WHERE SHELF2NODE = :1 ORDER BY SHELFNUMBER", (node_id,))
    shelves = cur.fetchall()
    print(f"\nShelves ({len(shelves)}):")
    for sh in shelves:
        print(f"  ShelfID: {sh[0]} | Name: '{sh[1]}' | Shelf#: {sh[2]} | TypeID: {sh[3]}")

    # 4. Slots
    cur.execute("""
        SELECT s.SLOTID, s.NAME, s.SLOTNUMBER, s.SLOT2SHELF
        FROM SLOT_O s
        JOIN SHELF_O sh ON s.SLOT2SHELF = sh.SHELFID
        WHERE sh.SHELF2NODE = :1
        ORDER BY s.SLOTNUMBER
    """, (node_id,))
    slots = cur.fetchall()
    print(f"\nSlots ({len(slots)}):")
    for sl in slots:
        print(f"  SlotID: {sl[0]} | Name: '{sl[1]}' | Slot#: {sl[2]} | ShelfID: {sl[3]}")

    # 5. Cards
    cur.execute("""
        SELECT c.CARDID, c.NAME, c.CARD2SHELFSLOT, s.NAME as SLOT_NAME, ct.NAME as CARDTYPE_NAME
        FROM CARD_O c
        LEFT JOIN SLOT_O s ON c.CARD2SHELFSLOT = s.SLOTID
        LEFT JOIN CARDTYPE_M ct ON c.CARD2CARDTYPE = ct.CARDTYPEID
        WHERE c.CARD2NODE = :1
        ORDER BY c.CARDID
    """, (node_id,))
    cards = cur.fetchall()
    print(f"\nCards ({len(cards)}):")
    for cd in cards:
        print(f"  CardID: {cd[0]} | Name: '{cd[1]}' | Slot: '{cd[3]}' (SlotID: {cd[2]}) | Type: '{cd[4]}'")

    # 6. Ports on Device
    cur.execute("""
        SELECT p.PORTID, p.NAME, p.PORTNUMBER, p.PORT2CARD, c.NAME as CARD_NAME, pt.NAME as PORTTYPE_NAME
        FROM PORT_O p
        LEFT JOIN CARD_O c ON p.PORT2CARD = c.CARDID
        LEFT JOIN PORTTYPE_M pt ON p.PORT2PORTTYPE = pt.PORTTYPEID
        WHERE p.PORT2NODE = :1
        ORDER BY p.PORTNUMBER
    """, (node_id,))
    ports = cur.fetchall()
    print(f"\nActual Ports in PORT_O ({len(ports)}):")
    actual_port_map = {}
    for pt in ports:
        card_info = f"Card: '{pt[4]}'" if pt[3] else "Chassis Port"
        actual_port_map[pt[2]] = pt[1]
        print(f"  PortID: {pt[0]} | Name: '{pt[1]}' | Port#: {pt[2]} | {card_info} | Type: '{pt[5]}'")

    # 7. Expected Naming in NODE_NAMING
    cur.execute("""
        SELECT SEQUENCE, PORTNAME, SLOTNAME
        FROM NODE_NAMING
        WHERE NODENAMING2NODEDEF = :1
        ORDER BY TO_NUMBER(REGEXP_SUBSTR(SEQUENCE, '^[0-9]+')), SEQUENCE
    """, (nodedef_id,))
    namings = cur.fetchall()
    print(f"\nExpected Naming from NODE_NAMING (Subtype: {nodedef_id}):")
    expected_port_map = {}
    for nm in namings:
        seq, p_name, s_name = nm
        if p_name:
            expected_port_map[int(seq) if seq.isdigit() else seq] = p_name
        print(f"  Seq {seq}: Port='{p_name}' | Slot='{s_name}'")

    # 8. Automated Discrepancy Analysis
    print(f"\n--- DISCREPANCY AUDIT ---")
    mismatches = 0
    for p_num, p_name in actual_port_map.items():
        exp_name = expected_port_map.get(p_num)
        if exp_name and exp_name != p_name:
            print(f"  [MISMATCH] Port#{p_num}: Actual='{p_name}' != Expected='{exp_name}'")
            mismatches += 1
        elif not exp_name:
            print(f"  [EXTRA] Port#{p_num} ('{p_name}') has no NODE_NAMING sequence match.")
        else:
            print(f"  [OK] Port#{p_num} matches ('{p_name}')")

    if mismatches == 0:
        print("  All existing ports match NODE_NAMING configuration.")
    else:
        print(f"  Found {mismatches} mismatch(es)! Possible naming typo or stale port.")

    # 9. Recent TMF API Calls for Node
    print(f"\n--- RECENT TMF API ACTIVITY FOR THIS DEVICE ---")
    cur.execute("""
        SELECT TMFAPILOGID, URI, TIMESTAMP,
               DBMS_LOB.SUBSTR(REQUEST, 400, 1),
               DBMS_LOB.SUBSTR(RESPONSE, 400, 1)
        FROM TMFAPILOG
        WHERE TMFAPILOGID >= (SELECT NVL(MAX(TMFAPILOGID), 0) - 3000 FROM TMFAPILOG)
          AND (DBMS_LOB.INSTR(REQUEST, :1) > 0 OR DBMS_LOB.INSTR(RESPONSE, :2) > 0
               OR DBMS_LOB.INSTR(REQUEST, :3) > 0 OR DBMS_LOB.INSTR(RESPONSE, :4) > 0)
        ORDER BY TMFAPILOGID ASC
    """, (str(node_id), str(node_id), node_name, node_name))
    logs = cur.fetchall()
    if not logs:
        print("  No recent TMF API calls found for this device in the last 3000 transactions.")
    for lg in logs:
        print(f"  [{lg[0]}] {lg[2]} {lg[1]}")
        if lg[3]: print(f"     REQ : {lg[3][:120]}...")
        if lg[4]: print(f"     RESP: {lg[4][:120]}...")

def triage_subtype(cur, subtype_identifier):
    print(f"\n{'='*75}")
    print(f"TRIAGE FOR SUBTYPE: {subtype_identifier}")
    print(f"{'='*75}")

    if str(subtype_identifier).isdigit():
        cur.execute("SELECT NODEDEFID, NAME, NODEDEF2NODETYPE, SHELFSIZE FROM NODEDEF_M WHERE NODEDEFID = :1", (int(subtype_identifier),))
    else:
        cur.execute("SELECT NODEDEFID, NAME, NODEDEF2NODETYPE, SHELFSIZE FROM NODEDEF_M WHERE UPPER(NAME) LIKE UPPER(:1)", (f"%{subtype_identifier.strip()}%",))
    rows = cur.fetchall()
    if not rows:
        print(f"[X] Subtype '{subtype_identifier}' NOT FOUND in NODEDEF_M!")
        return

    for nodedef_id, name, nodetype_id, shelf_size in rows:
        print(f"\nSubtype ID  : {nodedef_id}")
        print(f"Name        : {name}")
        print(f"NodeType ID : {nodetype_id} | ShelfSize: {shelf_size}")

        # Shelves & Slots in metadata
        cur.execute("""
            SELECT ns.NDST2SHELFTYPE, st.NAME, ns.COUNT
            FROM NODEDEFSHELFTYPE ns
            JOIN SHELFTYPE_M st ON ns.NDST2SHELFTYPE = st.SHELFTYPEID
            WHERE ns.NDST2NODEDEF = :1
        """, (nodedef_id,))
        shelves = cur.fetchall()
        print(f"Shelves ({len(shelves)}):")
        for sh in shelves:
            print(f"  ShelfType: {sh[0]} ('{sh[1]}') Count={sh[2]}")
            cur.execute("""
                SELECT ss.STST2SLOTTYPE, sl.NAME, ss.COUNT, ss.SEQUENCE
                FROM SHELFTYPESLOTTYPE ss
                JOIN SLOTTYPE_M sl ON ss.STST2SLOTTYPE = sl.SLOTTYPEID
                WHERE ss.STST2SHELFTYPE = :1
                ORDER BY ss.SEQUENCE
            """, (sh[0],))
            slots = cur.fetchall()
            for sl in slots:
                print(f"    Slot: Type {sl[0]} ('{sl[1]}') Count={sl[2]} Seq={sl[3]}")

        # Chassis Ports in PORTDEF_M
        cur.execute("""
            SELECT p.SEQUENCE, p.NAME, pt.NAME as PORTTYPE, p.COUNT
            FROM PORTDEF_M p
            JOIN PORTTYPE_M pt ON p.PORTDEF2PORTTYPE = pt.PORTTYPEID
            WHERE p.PORTDEF2NODEDEF = :1
            ORDER BY p.SEQUENCE
        """, (nodedef_id,))
        ports = cur.fetchall()
        print(f"Chassis Ports in PORTDEF_M ({len(ports)}):")
        for pt in ports:
            print(f"  Seq {pt[0]}: Name='{pt[1]}' Type='{pt[2]}' Count={pt[3]}")

        # NODE_NAMING
        cur.execute("""
            SELECT SEQUENCE, PORTNAME, SLOTNAME
            FROM NODE_NAMING
            WHERE NODENAMING2NODEDEF = :1
            ORDER BY TO_NUMBER(REGEXP_SUBSTR(SEQUENCE, '^[0-9]+')), SEQUENCE
        """, (nodedef_id,))
        namings = cur.fetchall()
        print(f"NODE_NAMING ({len(namings)} records):")
        for nm in namings:
            print(f"  Seq {nm[0]}: Port='{nm[1]}' | Slot='{nm[2]}'")

        # NAMING_NODE_EXT_O
        cur.execute("""
            SELECT ext.NAMINGID, ext.DEVICEPORT_NAME, ext.SHELFSLOT_NAME, ext.MODEL
            FROM NAMING_NODE_EXT_O ext
            JOIN NAMING_O n ON ext.NAMINGID = n.NAMINGID
            WHERE n.NAME = :1
        """, (name,))
        ext_rows = cur.fetchall()
        for ex in ext_rows:
            print(f"NAMING_NODE_EXT_O: NamingID={ex[0]} Model='{ex[3]}'")
            print(f"  Ports: {ex[1]}")
            print(f"  Slots: {ex[2]}")

def audit_keyword(cur, keyword):
    print(f"\n{'='*75}")
    print(f"AUDITING SUBTYPES MATCHING: '{keyword}'")
    print(f"{'='*75}")
    cur.execute("""
        SELECT n.NODEDEFID, n.NAME,
               NVL(p.p_count, 0) as p_count,
               NVL(nn.named_p_count, 0) as named_p_count,
               NVL(nn.named_s_count, 0) as named_s_count,
               NVL(nn.has_typo, 0) as has_typo
        FROM NODEDEF_M n
        LEFT JOIN (
            SELECT PORTDEF2NODEDEF, COUNT(*) as p_count
            FROM PORTDEF_M
            GROUP BY PORTDEF2NODEDEF
        ) p ON n.NODEDEFID = p.PORTDEF2NODEDEF
        LEFT JOIN (
            SELECT NODENAMING2NODEDEF,
                   COUNT(CASE WHEN PORTNAME IS NOT NULL THEN 1 END) as named_p_count,
                   COUNT(CASE WHEN SLOTNAME IS NOT NULL THEN 1 END) as named_s_count,
                   MAX(CASE WHEN PORTNAME = 'P3-RJ45-B' THEN 1 ELSE 0 END) as has_typo
            FROM NODE_NAMING
            GROUP BY NODENAMING2NODEDEF
        ) nn ON n.NODEDEFID = nn.NODENAMING2NODEDEF
        WHERE UPPER(n.NAME) LIKE UPPER(:1)
        ORDER BY n.NODEDEFID
    """, (f"%{keyword}%",))
    rows = cur.fetchall()
    print(f"Found {len(rows)} matching subtype(s):\n")

    for nid, name, p_count, named_p_count, named_s_count, has_typo in rows:
        typo_flag = " [!] TYPO P3-RJ45-B FOUND!" if has_typo == 1 else ""
        status = "[OK]" if named_p_count > 0 or p_count == 0 else "[WARN: NO NAMED PORTS]"
        print(f"ID: {nid:<10} | Ports: {p_count:<2} (Named: {named_p_count:<2}) | Slots: {named_s_count:<2} | {status} {name}{typo_flag}")

def search_log(cur, query):
    print(f"\n{'='*75}")
    print(f"SEARCHING RECENT TMFAPILOG FOR: '{query}'")
    print(f"{'='*75}")
    cur.execute("""
        SELECT TMFAPILOGID, URI, TIMESTAMP,
               DBMS_LOB.SUBSTR(REQUEST, 400, 1),
               DBMS_LOB.SUBSTR(RESPONSE, 400, 1)
        FROM TMFAPILOG
        WHERE TMFAPILOGID >= (SELECT NVL(MAX(TMFAPILOGID), 0) - 4000 FROM TMFAPILOG)
          AND (DBMS_LOB.INSTR(REQUEST, :1) > 0 OR DBMS_LOB.INSTR(RESPONSE, :2) > 0)
        ORDER BY TMFAPILOGID DESC
    """, (query, query))
    rows = cur.fetchall()
    print(f"Found {len(rows)} recent log transaction(s):\n")
    for r in rows:
        print(f"[{r[0]}] {r[2]} {r[1]}")
        if r[3]: print(f"  REQ : {r[3][:140]}...")
        if r[4]: print(f"  RESP: {r[4][:140]}...")

def triage_payload(cur, payload_input):
    import os, json
    print(f"\n{'='*75}")
    print(f"TRIAGE FOR INCOMING TMF / ORDER PAYLOAD")
    print(f"{'='*75}")
    content = payload_input
    if os.path.exists(payload_input):
        with open(payload_input, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    try:
        data = json.loads(content)
    except Exception as e:
        print(f"[X] Failed to parse JSON: {e}")
        return

    device_name = data.get('name')
    spec_info = data.get('resourceSpecification', {})
    spec_name = spec_info.get('name') if isinstance(spec_info, dict) else str(spec_info)

    requested_ports = []
    for rel in data.get('resourceRelationship', []):
        res = rel.get('resource', {})
        rel_type = str(rel.get('relationshipType', '')).lower()
        if rel_type == 'port' or res.get('@type') == 'Port':
            pname = res.get('name')
            if pname:
                requested_ports.append(pname)

    print(f"Payload Device Name : {device_name}")
    print(f"Payload Spec/Subtype: {spec_name}")
    print(f"Requested Ports ({len(requested_ports)}): {', '.join(requested_ports) if requested_ports else 'None specified'}")

    if device_name:
        triage_node(cur, device_name)
        if requested_ports:
            print(f"\n--- PAYLOAD PORT CORRELATION CHECK ---")
            cur.execute("""
                SELECT NAME FROM PORT_O 
                WHERE PORT2NODE = (SELECT NODEID FROM NODE_O WHERE UPPER(NAME) = UPPER(:1))
            """, (device_name.strip(),))
            actual_ports = {r[0] for r in cur.fetchall()}
            for rp in requested_ports:
                if rp in actual_ports:
                    print(f"  [OK] Payload port '{rp}' MATCHES an existing port in PORT_O.")
                else:
                    print(f"  [!] MISMATCH: Payload expects port '{rp}', but it is MISSING on device!")
                    print(f"      Existing ports on device: {', '.join(sorted(actual_ports))}")
    elif spec_name:
        triage_subtype(cur, spec_name)

def audit_naming_anomalies(cur):
    print(f"\n{'='*75}")
    print(f"DATABASE-WIDE AUDIT FOR NODE_NAMING ANOMALIES")
    print(f"{'='*75}")
    cur.execute("""
        SELECT nn.NODENAMING2NODEDEF, n.NAME, nn.PORTNAME, COUNT(*)
        FROM NODE_NAMING nn
        JOIN NODEDEF_M n ON nn.NODENAMING2NODEDEF = n.NODEDEFID
        WHERE nn.PORTNAME IS NOT NULL
        GROUP BY nn.NODENAMING2NODEDEF, n.NAME, nn.PORTNAME
        HAVING COUNT(*) > 1
        ORDER BY n.NAME
    """)
    dups = cur.fetchall()
    print(f"Found {len(dups)} duplicate port naming entries across subtypes:\n")
    for d in dups[:20]:
        print(f"  Subtype {d[0]} ('{d[1]}'): duplicate port '{d[2]}' ({d[3]} occurrences)")
    if len(dups) > 20:
        print(f"  ... and {len(dups) - 20} more.")

def main():
    parser = argparse.ArgumentParser(description="ARM Device & Subtype Diagnostic Triage Utility")
    parser.add_argument('--node', help="Node Name (e.g. LON-CA-001828) or Node ID (e.g. 1314598)")
    parser.add_argument('--subtype', help="Subtype Name or Subtype ID to inspect")
    parser.add_argument('--audit', help="Audit all subtypes matching a keyword (e.g. 'MetroNID')")
    parser.add_argument('--audit-anomalies', action='store_true', help="Audit DB for duplicate/broken port naming")
    parser.add_argument('--payload', help="JSON payload string or path to JSON file for instant correlation")
    parser.add_argument('--log', help="Search recent TMFAPILOG for order/device keyword")
    parser.add_argument('--env', default='SIT2', choices=['SIT2', 'TEST2', 'DEV2'], help="Environment (default: SIT2)")
    parser.add_argument('--dsn', help="Custom Oracle DSN")
    parser.add_argument('--user', help="Custom Oracle User")
    parser.add_argument('--password', help="Custom Oracle Password")

    args = parser.parse_args()
    if not (args.node or args.subtype or args.audit or args.audit_anomalies or args.payload or args.log):
        parser.print_help()
        sys.exit(1)

    try:
        conn = get_connection(args.env, args.dsn, args.user, args.password)
        cur = conn.cursor()
    except Exception as e:
        print(f"[X] Database Connection Failed to {args.env}: {e}")
        sys.exit(1)

    try:
        if args.node:
            triage_node(cur, args.node)
        elif args.subtype:
            triage_subtype(cur, args.subtype)
        elif args.audit:
            audit_keyword(cur, args.audit)
        elif args.audit_anomalies:
            audit_naming_anomalies(cur)
        elif args.payload:
            triage_payload(cur, args.payload)
        elif args.log:
            search_log(cur, args.log)
    finally:
        conn.close()

if __name__ == '__main__':
    main()
