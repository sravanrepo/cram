# ARM Oracle Database Knowledge Base & Reference Manual

## 1. Domain Context & Terminology
- **ARM**: Stands for **Application Resource Management** (Enterprise Resource & Capacity Management System). It tracks physical & logical assets (Data Centers, Racks, Shelves, Slots, Line Cards, Devices, and Port/Bandwidth Capacity).
- **ARM is NOT CPU hardware**: Never interpret ARM as Arm processor architecture.
- **Underlying Engine**: Oracle Database (19c / 21c Enterprise Edition).

---

## 2. Core Database Schema & Entities

### Table: `ARM_CONTAINERS`
Stores parent physical and logical structures.
- `CONTAINER_ID` (NUMBER, PK): Unique internal sequence.
- `CONTAINER_CODE` (VARCHAR2): Human-readable code (e.g., `COLT-LON-DC1-RK04`).
- `CONTAINER_NAME` (VARCHAR2): Descriptive name of the site, room, or rack.
- `CONTAINER_TYPE` (VARCHAR2): Classification (`Container Type`, `Slot Shelf Type`, `RACK`, `DC`, `POP`).
- `STATUS` (VARCHAR2): `ACTIVE`, `PLANNED`, `DECOMMISSIONED`.
- `SITE_CODE` (VARCHAR2): Site identifier (e.g., `LON01`, `FRA02`).
- `MAX_CAPACITY_UNITS` & `USED_CAPACITY_UNITS`: High-level slot/space metrics.

### Table: `ARM_ENTITIES`
Represents physical devices, chassis, modules, and line items.
- `ENTITY_ID` (NUMBER, PK): Primary key identifier.
- `ENTITY_CODE` (VARCHAR2): Serial/inventory identifier.
- `ENTITY_NAME` (VARCHAR2): e.g., `Cisco ASR 9000`, `Juniper MX480`.
- `ENTITY_TYPE` (VARCHAR2): `Device Type`, `Router`, `Switch`, `Slot Shelf Type`.
- `SUBTYPE_NAME` (VARCHAR2): Specific variant or hardware subtype.
- `CONTAINER_ID` (NUMBER, FK): Foreign key referencing the parent `ARM_CONTAINERS`.
- `OPERATIONAL_STATE`: `IN_SERVICE`, `RESERVED`, `MAINTENANCE`, `FAULT`.

### Table: `ARM_CAPACITY_ALLOCATIONS`
Tracks multi-dimensional resource metrics per container.
- `RESOURCE_METRIC`: Type of capacity (`RACK_UNITS`, `POWER_KW`, `BANDWIDTH_GBPS`, `PORTS`).
- `TOTAL_PROVISIONED`: Total capacity ceiling.
- `CURRENT_USAGE`: Currently consumed resources.
- `RESERVED_CAPACITY`: Temporarily locked capacity.
- `AVAILABLE_CAPACITY`: Auto-calculated virtual column (`TOTAL_PROVISIONED - CURRENT_USAGE - RESERVED_CAPACITY`).
- `UTILIZATION_PCT`: Real-time percentage (`(CURRENT_USAGE / TOTAL_PROVISIONED) * 100`).

### Table: `ARM_RELATIONSHIPS`
Describes parent-child and containment hierarchies.
- `RELATION_TYPE`: `CONTAINS`, `MOUNTS_ON`, `CONNECTED_TO`.

---

## 3. Standard Stored Procedures

### Procedure: `ARM_OPERATIONS_PKG.GET_CONTAINER_CAPACITY_REPORT`
- **Input**: `p_container_code` (VARCHAR2) - The unique code of the container.
- **Output**: `p_out_record` (SYS_REFCURSOR) - Returns all capacity allocations, usage metrics, and utilization percentages for the container.
- **Trigger Example**: When the user asks: *"What is the capacity breakdown for container [CODE]?"* or *"How much power/space is left in [CODE]?"*

### Procedure: `ARM_OPERATIONS_PKG.RESERVE_CONTAINER_CAPACITY`
- **Input**:
  - `p_container_code` (VARCHAR2)
  - `p_metric` (VARCHAR2, e.g., `RACK_UNITS`, `POWER_KW`)
  - `p_reserve_amount` (NUMBER)
- **Output**: `p_status_message` (VARCHAR2) - Returns confirmation (`SUCCESS`) or rejection (`FAILED: Insufficient capacity`).
- **Trigger Example**: When the user requests to reserve resources for planned provisioning.

---

## 4. Query & Response Guidelines for the Agent
1. **Always Use Bound/Parameterized Queries**: When querying through Power Automate, pass variables into query placeholders to prevent injection.
2. **Read-Only by Default**: Do not execute or propose DDL (`DROP`, `ALTER`) or unrestricted DML (`UPDATE`, `DELETE`) without explicit parameters.
3. **Data Formatting**: Present capacity and inventory reports in Markdown tables with percentage indicators.

---

## 5. Circuit Extension Tables, TMF639 REST Services & Metadata Apply Architecture

### 5.1 Architecture Overview
- **REST Service Layer**: `inv-rest-cust-ear-23.09.ear` (deployed on WebLogic `CramerServer` port 8112).
  - Path: `CustomizationKit/resource-manager/inventory-rest-services/inv-rest-cust-jar/src/main/java/com/amdocs/oss/irs/cust/mapper/CustomTMF639Mapper.java`.
  - Inbound (`POST` / `PATCH`): Maps external TMF639 `Characteristic` objects into internal `BiomeObject` attributes.
  - Outbound (`GET`): Reads internal `BiomeObject` attributes and maps them back into external TMF639 `Characteristic` objects.
- **Circuit Extension Tables**:
  - `ETHERNET_BEARER_EXT_O` (Physical base table storing custom circuit attributes).
  - `ETHERNET_BEARER_EXT_VW` (Context-aware view wrapping `_O` with `INSTEAD OF` triggers: `ETHERNET_BEARER_EXT_I`, `ETHERNET_BEARER_EXT_U`, `ETHERNET_BEARER_EXT_D`).
  - Related metadata dictionary tables:
    - `ATTRIBUTE_M`: Column name, attribute ID, attribute type (`NUMBER`, `ATTRTYPEENUM=5`).
    - `FEATUREEXTENSIONCLASS`: Maps extension class (e.g. `1900000056` for `ETHERNET_BEARER_EXT`) to attribute with order number.
    - `ENUMERATIONDEF_M` & `ENUMERATIONVALUE_M`: Defines allowable string values and their numeric sequences.

### 5.2 Root Causes & Critical Traps

#### Trap 1: XML Tag Collision in Custom Mapper
- **Symptom**: Outbound GET or DB update fails because WorkOrder XML contains raw string tag instead of numeric sequence.
- **Cause**: If custom attributes are left in `characteristics` before calling `super.mapCreateToInternal()` / `super.mapUpdateToInternal()`, the default Biome mapper serializes them as raw strings:
  `<ATTRIBUTE NAME="ENCRYPTION TECHNOLOGY" VALUE="Quantum Safe PSK - Dedicated"/>`
  If custom code also sets `obj.setLongAttribute("ENCRYPTION_TECHNOLOGY", seq)`, duplicate or conflicting XML tags are generated.
- **Rule / Fix**: Always filter out custom characteristics before calling `super.map...ToInternal()`:
  ```java
  List<Characteristic> customChars = extractAndRemoveCharacteristics(characteristics);
  super.mapCreateToInternal(request, circuit);
  mapCustomToInternal(customChars, circuit);
  ```

#### Trap 2: CramerServer Dynamic SQL Omission (Metadata Desync)
- **Symptom**: WorkOrder XML cleanly contains:
  `<ATTRIBUTE NAME="ENCRYPTION_TECHNOLOGY" VALUE="2" ENUMVALUE="Quantum Safe PSK - Dedicated"/>`
  The circuit is created successfully (HTTP 201), but `ENCRYPTION_TECHNOLOGY` in `ETHERNET_BEARER_EXT_O` remains `NULL`, while older attributes (e.g. `ENC_ENCRYPTION_KEY_MANAGEMENT`) are populated.
- **Root Cause**: During WorkOrder Apply, CramerServer constructs its SQL dynamically:
  `UPDATE ETHERNET_BEARER_EXT SET ORDER_NUM=..., ENC_ENCRYPTION_KEY_MANAGEMENT=... WHERE CIRCUITID = :CIRCUITID`
  CramerServer generates this SQL from its **in-memory compiled metadata model**, NOT from the raw Oracle table columns. If a new column exists in the Oracle database but the metadata delta has not been distributed/imported into CramerServer's runtime repository, CramerServer silenty omits the column from its dynamic SQL.
- **Rule / Fix**: Whenever adding new extension attributes or column enumerations, deploying only the application EAR (`ARM_2309_CUSTOM_REFRESH`) is insufficient. A metadata distribution step (`BuildDistribute -op distribute`) must be executed to update the server's runtime catalog.

### 5.3 Deployment Pipeline & Job Differences
- **`ARM_2309_CUSTOM_REFRESH` (Standalone App Refresh)**:
  - Deploys: EARs (`inv-rest-cust-ear`, `rm-wizards-ear`, `rp-wizards-ear`), WARs, JARs.
  - Runs: `install_all.sql`, `PKGNAMING.h`, `PKGNAMING.sql`.
  - **Does NOT** distribute Core RM-Repo metadata deltas.
- **`ARM_and_ANN_Refresh_flow` (Full Environment Refresh)**:
  - Step 1: `ARM_2309_CUSTOM_REFRESH`:
    - Automatically executes `com.amdocs.oss.ide.mm.cli.BuildDistribute -op distribute` for `metadata/Repo/RM-Repo` directly against schema `CRAMER` in Oracle.
    - Redeploys all EARs, WARs, and JARs.
  - Step 2: `ANN_2309_CUSTOM_REFRESH`:
    - Executes `BuildDistribute -op distribute` for ANN metadata (`corestaticannrepo`) against `ANN01METADATA`.
    - Redeploys ANN EARs and uploads ANN Forms customization.
- **Verification Rule**:
  - Always verify outbound GET via:
    `GET /inventory-rest-services/resourceInventoryManagement/v400/circuit/<circuit_id>` with Bearer token authentication.

