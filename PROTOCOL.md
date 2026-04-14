# Muninn — Wire Protocol Specification

## Conventions

- **Byte order:** big-endian (network order) for all multi-byte integers
- **Text encoding:** UTF-8 for all plaintext message content
- **MAC addresses:** 6 bytes, MSB first (e.g. `AA:BB:CC:DD:EE:FF` → `0xAA 0xBB 0xCC 0xDD 0xEE 0xFF`)
- **UUIDs:** 16 bytes, big-endian (RFC 4122 binary representation)

---

## Frame Header

Every frame uses the same 3-byte header:

```
[ 1 byte: type ][ 2 bytes: payload_length ][ N bytes: payload ]
```

| Field          | Size    | Type   | Notes                  |
|----------------|---------|--------|------------------------|
| type           | 1 byte  | uint8  | Frame type identifier  |
| payload_length | 2 bytes | uint16 | Length of payload in bytes |
| payload        | N bytes | raw    | Type-specific content  |

Maximum payload: 65,535 bytes.

---

## Frame Types

| Type      | Value  | Direction    | Encrypted |
|-----------|--------|--------------|-----------|
| Handshake | `0x01` | Both → Both  | No        |
| Message   | `0x02` | Sender → Receiver | Partially (see below) |
| ACK       | `0x03` | Receiver → Sender | No |

---

## Handshake Frame (`0x01`)

Sent by both sides immediately after RFCOMM connection. No encryption (shared secret does not yet exist).

**Payload:**

```
[ 32 bytes: X25519 public key ]
```

**Sequence:**

1. Both sides generate (or reuse) an X25519 keypair
2. Both sides send a handshake frame containing their public key
3. Both sides receive the peer's public key
4. Both sides compute the shared secret via ECDH (X25519)
5. All subsequent frames use NaCl Box encryption (XSalsa20-Poly1305)

Order of send/receive does not matter — both sides send and read concurrently. No sequencing dependency.

---

## Message Frame (`0x02`)

**Payload:**

```
[ 16 bytes: group_id   ]  — UUID v4, zeroed for 1:1 DMs
[ 16 bytes: msg_id     ]  — UUID v4, unique per message
[  6 bytes: sender_id  ]  — BT MAC of originating device
[  6 bytes: final_dest ]  — BT MAC of intended recipient
[  4 bytes: timestamp  ]  — uint32, unix seconds (UTC)
[ 24 bytes: nonce      ]  — random, generated per message
[  N bytes: ciphertext ]  — NaCl Box encrypted UTF-8 text
```

Total header before ciphertext: 72 bytes.

**Encryption details:**

- Metadata fields (group_id through timestamp) are **plaintext** within the payload
- Only the message text is encrypted: `Box(plaintext, nonce, shared_secret)`
- The nonce is generated as 24 cryptographically random bytes per message
- The nonce is transmitted alongside the ciphertext (not derived)
- Ciphertext includes Poly1305 authentication tag (16 bytes, appended by NaCl)

**group_id in 1:1 mode:** set to `00000000-0000-0000-0000-000000000000` (16 zero bytes).

---

## ACK Frame (`0x03`)

Sent by the receiver upon receiving and successfully decrypting a message.

**Payload:**

```
[ 16 bytes: msg_id ]  — echoed from the message being acknowledged
[  6 bytes: from   ]  — BT MAC of the acknowledging device
```

Total payload: 22 bytes.

---

## Connection Lifecycle

```
Device A                              Device B
────────────────────────────────────────────────
         ◄── RFCOMM connect ──►

send Handshake(pubkey_A)  ──────────►  recv Handshake(pubkey_A)
recv Handshake(pubkey_B)  ◄──────────  send Handshake(pubkey_B)

         ECDH → shared secret

send Message(encrypted)   ──────────►  recv Message → decrypt
                          ◄──────────  send ACK(msg_id)

recv Message(encrypted)   ◄──────────  send Message(encrypted)
send ACK(msg_id)          ──────────►
```

---

## Reconnection

On socket error or EOF:

1. Both sides return to listen/connect state
2. New RFCOMM connection established
3. Fresh handshake (static keys → same shared secret)
4. Sender resends all messages that never received an ACK
5. Receiver checks `msg_id` against previously seen messages:
   - **Already seen:** silently drop, send ACK again
   - **New:** process normally, send ACK

---

## Simultaneous Connection Tiebreak

If both devices initiate a connection at the same time, two RFCOMM sockets form. Resolution:

- Compare local BT MAC addresses (as 6-byte unsigned integers)
- The socket initiated by the device with the **higher** MAC address is closed
- Both sides apply this rule independently — result is deterministic

---

## Service Discovery

**Service UUID:** `320bcf9c-94fe-46f4-b9bf-83535cafcd55`

Registered via SDP (Service Discovery Protocol). Connecting device performs SDP lookup to find the RFCOMM channel number for this UUID.

---

## Crypto Summary

| Primitive         | Algorithm                    |
|-------------------|------------------------------|
| Key exchange      | X25519 (Curve25519 ECDH)     |
| Symmetric cipher  | XSalsa20                     |
| Authentication    | Poly1305                     |
| Combined          | NaCl Box (all three above)   |
| Nonce             | 24 bytes, random per message |
| Public key        | 32 bytes (X25519)            |
