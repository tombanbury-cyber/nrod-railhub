# East Kent TD S-Class Decoding with SMART 1

## Summary

The useful outcome is much simpler than the route taken to discover it.

> For the East Kent data examined, the TD S-class address byte maps directly to the SMART serial byte number once the TD address is interpreted as hexadecimal and the SMART document byte number as decimal. The TD bit number then maps directly to the SMART bit number.

For example:

```text
TD address 31 hex = 49 decimal = SMART byte 049
```

SMART byte 049 contains:

```text
bit 0 = S5116
bit 1 = S5117
bit 2 = S5118
bit 3 = S5120
bit 4 = S5122
bit 5 = S5123
bit 6 = S5124
bit 7 = S5125
```

---

## Concise guide to what worked

### 1. Expand all TD S-class messages into byte state

- `SF` already represents one byte.
- `SG` and `SH` need to be expanded into their constituent consecutive bytes.
- Store the normalized result in `td_signal_bytes`.

### 2. Store TD addresses numerically as hex-decoded integers

Examples:

```text
TD address "31" -> address_int 49
TD address "58" -> address_int 88
```

This turned out to be crucial.

### 3. Import the SMART document into a byte/bit lookup table

The SMART source is organised as:

```text
BYTE : BIT : FUNCTION
```

Each serial byte contains up to eight Boolean signalling functions.

### 4. Compare TD `address_int` directly to SMART `byte_dec`

This was the breakthrough:

```text
td_signal_bytes.address_int = smart_serial_bit_map.byte_dec
```

### 5. Compare bit number directly

If TD byte bit 3 changes, that corresponds to SMART bit 3 for the same byte.

### 6. Validate using observed bit activity

We compared the bits that actually toggled in the live TD feed with the bits documented in SMART.

The correspondence was extremely strong.

### 7. Repair extraction errors in the SMART CSV

Several functions were lost when the source PDF crossed page boundaries:

```text
byte 019 bit 3 -> LSGSTONEAHB
byte 025 bit 4 -> P2180BN
byte 032 bit 5 -> R5060CSP
byte 038 bit 6 -> S5086
```

### 8. Keep undocumented live bits separate

A small number of TD bits are active even though the old SMART document leaves them blank:

```text
5D bit 3
65 bits 2–6
```

These should be treated as:

```text
active-but-undocumented
```

rather than guessed.

---

## What did not help much

The behavioural classification work was useful for understanding the data, but it was not needed for the final mapping.

We examined:

```text
single-bit transitions
two-bit transitions
complementary bit pairs
possible Normal/Reverse point pairs
```

This confirmed that most TD S-class bytes behave like packed Boolean indications, but it did not reveal the mapping.

The important correction was realizing that:

```text
TD address "88" = hexadecimal 0x88 = decimal 136
SMART byte 088 = decimal 88 = TD address 0x58
```

So the numbering systems were being interpreted differently.

---

## Core database structure

### `td_signal_bytes`

Useful normalized fields:

```text
timestamp
area_id
address_int
value_int
address
value
source_type
raw_message_id
```

### `smart_serial_bit_map`

Important lookup fields:

```text
byte_dec
bit
interlocking
section
function_type
function
```

The core join is:

```sql
ON smart_serial_bit_map.byte_dec = td_signal_bytes.address_int
```

Individual bit state is:

```sql
(value_int & (1 << bit)) <> 0
```

---

## Current decoded-state view

```sql
CREATE VIEW IF NOT EXISTS v_td_signal_function_state AS
SELECT
    s.area_id,

    printf('%02X', s.address) AS td_address,
    s.address AS address_int,

    m.bit,
    printf('%02X', (1 << m.bit)) AS bit_mask,

    m.interlocking,
    m.section,
    m.function_type,
    m.function,
    m.location_context,

    CASE
        WHEN (s.value & (1 << m.bit)) <> 0
        THEN 1
        ELSE 0
    END AS state,

    s.value AS byte_value_int,
    printf('%02X', s.value) AS byte_value_hex,

    s.updated_at,
    s.source_type

FROM td_signal_state s

JOIN smart_serial_bit_map m
  ON m.byte_dec = s.address

WHERE s.area_id = 'EK'
  AND m.function IS NOT NULL
  AND m.function <> '';
```

Example:

```sql
SELECT *
FROM v_td_signal_function_state
WHERE function = 'S5116';
```

---

## Decoded transition view

```sql
CREATE VIEW IF NOT EXISTS v_td_signal_function_transitions AS
WITH ordered AS (
    SELECT
        id,
        timestamp,
        area_id,
        address,
        address_int,
        value,
        value_int,
        source_type,
        raw_message_id,

        LAG(value_int) OVER (
            PARTITION BY area_id, address_int
            ORDER BY timestamp, id
        ) AS previous_value

    FROM td_signal_bytes
),

byte_changes AS (
    SELECT
        *,
        ((previous_value | value_int)
         - (previous_value & value_int)) AS changed_bits

    FROM ordered

    WHERE previous_value IS NOT NULL
      AND previous_value <> value_int
)

SELECT
    c.id,
    c.timestamp,
    c.area_id,
    c.address AS td_address,
    c.address_int,

    m.bit,
    m.interlocking,
    m.section,
    m.function_type,
    m.function,

    CASE
        WHEN (c.previous_value & (1 << m.bit)) <> 0
        THEN 1 ELSE 0
    END AS previous_state,

    CASE
        WHEN (c.value_int & (1 << m.bit)) <> 0
        THEN 1 ELSE 0
    END AS new_state,

    c.source_type,
    c.raw_message_id

FROM byte_changes c

JOIN smart_serial_bit_map m
  ON m.byte_dec = c.address_int

WHERE c.area_id = 'EK'
  AND m.function IS NOT NULL
  AND m.function <> ''
  AND (c.changed_bits & (1 << m.bit)) <> 0;
```

SQLite does not support the `^` XOR operator, so XOR was implemented as:

```sql
(a | b) - (a & b)
```

---

## Example decode

Raw TD:

```text
SF,31,05
```

`05` is:

```text
00000101
```

So bits 0 and 2 are on.

SMART byte 049 says:

```text
bit 0 = S5116
bit 2 = S5118
```

Therefore:

```text
S5116 = ON
S5118 = ON
```

---

## Confidence level

### High confidence

```text
TD address_int == SMART byte_dec
TD bit == SMART bit
SMART lookup decodes real signalling functions
Most TD S-class bytes are packed Boolean indications
```

### Confirmed caveat

The SMART PDF is old and appears incomplete in a few positions.

### Not yet decoded

```text
meaning of undocumented active bits
whether newer SMART output schedules exist
full relationship between signals/routes/points and berth stepping
```

---

## Next phase

The byte format is now substantially decoded.

The next useful chain is:

```text
TD bit
  ↓
SMART function
  ↓
signal / route / point
  ↓
stepping-table berth
  ↓
train / headcode
```

That is where the East Kent stepping-table data becomes especially useful.
