PIKO IQ/PLENTICORE
KOSTAL Interface description
MODBUS (TCP) & SunSpec
with control information

Version
Release date: 20.11.

Revision Version: 2.

Valid from firmware version:
PIKO/PLENTICORE G1: UI 01.30.
PLENTICORE G2: SW 02.15.xxxxx
PLENTICORE G3: SW 3.06.00.xxxxx
PLENTICORE MP G3: SW 3.06.00.xxxxx

Content
1. Introduction
1.1 Disclaimer of liability
The activities described in this document may only be carried out by specialists who have the following
qualifications:

 (^) Knowledge of IP-based network protocols
 (^) Training in the installation and configuration of IT systems
 (^) Knowledge of the functionality and operation of inverters
 (^) Training in dealing with dangers and risks associated with the operation of electrical devices and
systems
 (^) Knowledge of and observance of this document
Any instance of misuse will result in the termination of the warranty, guarantee and general liability to the
fullest extent permitted by law of the manufacturer.
Nothing in this conditions limits or excludes the liability of KOSTAL for:
Death or personal injury resulting from its negligence; or fraud or fraudulent misrepresentation. KOSTAL
shall not under any circumstances whatever be liable for loss of profits; or any special, indirect or con-
sequential loss, costs, damages, charges or expenses.

1.2 Intended Use
The Modbus interface is designed for industrial use and has the following tasks:

 (^) Remote control of the grid management services of a PV system.
 (^) Remote-controlled query of measured values from a PV system.
 (^) Remote-controlled change of parameters of a PV system.
 (^) Remote charge and discharge control of hybrid and battery inverters
The Modbus interface can be used via the Modbus TCP protocol.
All components have to stay within their permitted operating and installation requirements. The products
should only be used in line with their documentation and the local applicable standards and directives.
Alterations to the product, e.g. changes or modifications, are prohibited. Any use of the product other
than that described in the Intended Use section does not qualify as the intended use. Tobserve all inst-
ructions contained therein.

1.3 Target group
These instructions are aimed at the aforementioned qualified specialist who needs information on the
Modbus interface in order to use it for industrial use and to create their own products.

1.4 Safety Information
This section contains safety information that must be observed at all times. Opening the Modbus inter-
face from extern can be results in the manipulation of the PV system data in the Ethernet network.

If KOSTAL solar inverters are connected to the Internet, there is a risk that unauthorized users can
access and manipulate the data of your PV system.

 (^) Set up a firewall.
 (^) Close unnecessary network ports in your router.
 (^) Remote access should only be done through a virtual private network (VPN).
 (^) Do not set up port forwarding for the Modbus ports used.
KOSTAL under no circumstances shall have any liability for any loss or damage invcurres by the use of
the Modbus interface. The use of the interface and reliance on the information is solely at the users risk.

1.5 MODBUS Protocol
MODBUS is an application layer messaging protocol, positioned at level 7 of the OSI model, which pro-
vides client/server communication between devices connected on different types of buses or networks.

The industry’s serial de facto standard since 1979, MODBUS continues to enable millions of automation
devices to communicate. Today, support for the simple and elegant structure of MODBUS continues
to grow. The Internet community can access MODBUS at a reserved system port 502 on the TCP/IP
stack.

MODBUS is a request/reply protocol and offers services specified by function codes. MODBUS function
codes are elements of MODBUS request/reply PDUs. The objective of this document is to describe the
function codes used within the framework of MODBUS transactions.

The MODBUS Application Protocol is currently used in the solar sector mainly for system communica-
tion in PV power plants. The MODBUS protocol has been developed for reading data from- or writing
data to clearly defined data areas.

1.6 Data Formats
The following data formats describe how data is to be interpreted. The data formats are important, for
example, for the display of data or for its further processing. The data formats are listed in the Format
column of the assignment tables.

U16 An unsigned integer 16-bit-value, 1 register
U32 An unsigned integer 32-bit-value, 2 registers
S16 A signed integer 16-bit-value, 1 register
S32 A signed integer 32-bit-value, 2 registers
MBD Multiple bytes data.
2. MODBUS protocol description
2.1 Application Layer
MODBUS is an application layer messaging protocol, positioned at level 7 of the OSI model, which pro-
vides client/server communication between devices connected on different types of buses or networks.

MODBUS application layer

MODBUS on TCP
TCP
IP
Ethernet II / 802.
Ethernet
physical layer
Master / Slave
EIA/TIA-232 or
EIA/TIA-
MODBUS+/HDLC
Physical layer
Other
Other
Abb. 1: MODBUS communication stack

MODBUS is an application layer messaging protocol for client/server communication between devices
connected on different types of buses or networks.

Scope of this document is the implementation TCP/IP over Ethernet. See MODBUS Messaging Imple-
mentation Guide V1.0a.

2.1.1 MODBUS frame

The MODBUS protocol defines a simple protocol data unit (PDU) independent of the underlying com-
munication layers. The mapping of MODBUS protocol on specific buses or network can introduce some
additional fields on the application data unit (ADU).

Additional address Function code Data Error check
ADU
PDU
Abb. 2: General MODBUS frame

Abbreviations:

ADU Application Data Unit
HDLC High Level Data Link Control
HMI Human Machine Interface
IETF Internet Engineering Task Force
I/O input/Output
IP Internet Protokoll
MAC Media Access Control
MB MODBUS Protokol
MBAP MODBUS Apllication Protokol
PDU Protocol Data Unit
PLC Programmable Logic Control
TCP Transmission Control Protocol
The MODBUS application data unit is built by the client that initiates a MODBUS transaction. The func-
tion indicates to the server what kind of action to perform.

The function code field of a MODBUS data unit is coded in one byte. Valid codes are in the range of

1 ... 255 decimal (the range 128 – 255 is reserved and used for exception responses). When a message
is sent from a Client to a Server device the function code field tells the server what kind of action to per-
form. Function code “0” is not valid.

2.1.2 Data Encoding

The MODBUS communication can be set to ‘little-endian (CDAB) Standard Modbus’ (default) or
‘big-endian (ABCD) Sunspec’ under Settings > Modbus/Sunspec (TCP).

Example for ‘big-endian (ABCD) Sunspec’:
This means that when a numerical quantity larger than a single byte is transmitted, the most significant
byte is sent first. So for

Register size value
16 - bits 0x1234 the first byte sent is 0x12, then 0x
2.1.3 Function code list

The following MODBUS commands are supported by the implemented MODBUS interface:

MODBUS command Function code Quantity of Registers^1
Read Holding Registers 0x03 1 to 125
Write Single Register 0x06 1
Write Multiple Registers 0x10 1 to 123
(^1) Register content is 16-bits width.

2.1.4 Read Holding Registers (0x03)

This function code is used to read the contents of a contiguous block of holding registers in the inverter.
The Request PDU specifies the starting register address and the number of registers. In the PDU Regis-
ters are addressed starting at zero. Therefore registers numbered1-16 are addressed as 0-15.

The register data in the response message are packed as two bytes per register, with the binary con-
tents right justified within each byte. For each register, the first byte contains the high order bits and the
second contains the low order bits.

Request

Function code 1 Byte 0x
Starting Address 2 Bytes 0x0000 to 0xFFFF
Quantity of Registers 2 Bytes 1 to 125 (0x7D)
Response

Function code 1 Byte 0x
Register value 1 Bytes 0x0000 to 0xFFFF
Byte count N^1 x 2 Bytes 1 to 125 (0x7D)
(^1) N = Quantity of Registers
Error
Error code 1 Byte 0x
Exception code 1 Bytes 01 or 02 or 03 or 04
Here is an example of a request to read registers 108 – 110:
Request Response
Field Name (hex) Field Name (hex)
Function 03 Function 03
Starting Address Hi 00 Byte Count 06
Starting Address Low 6B Register value Hi (108) 02
No. of Registers Hi 00 Register value Low (108) 2B
No. of Registers Low 03 Register value Hi (109) 00
Register value Low (109) 00
Register value Hi (110) 00
Register value Low (110) 64
The contents of register 108 are shown as the two byte values of 0x022B. The contents of registers 109
–110 are 0x0000 and 0x0064.

2.1.5 Write Single Register (0x06)

This function code is used to write a single holding register in the inverter.

The Request PDU specifies the address of the register to be written.

The normal response is an echo of the request, returned after the register contents have been written.

Request

Function code 1 Byte 0x
Register address 2 Bytes 0x0000 to 0xFFFF
Register value 2 Bytes 0x0000 to 0xFFFF
Response

Function code 1 Byte 0x
Register address 2 Bytes 0x0000 to 0xFFFF
Register value 2 Bytes 0x0000 to 0xFFFF
Error

Error code 1 Byte 0x
Exception code 1 Bytes 01 or 02 or 03 or 04
Here is an example of a request to write register 2 to 0x0003:

Request Response
Field Name (hex) Field Name (hex)
Function 06 Function 06
Register Address Hi 00 Register Address Hi 00
Register Address Low 01 Register Address Low 01
Register Value Hi 00 Register Value Hi 00
Register Value Low 03 Register Value Low 03
2.1.6 Write Multiple Register (0x10)

This function code is used to write a block of contiguous registers (1 to 123 registers) in a remote
device.
The requested written values are specified in the request data field. Data is packed as two bytes per
register.
The normal response returns the function code, starting address, and quantity of registers written.

Request

Function code 1 Byte 0x
Starting Address 2 Bytes 0x0000 to 0xFFFF
Quantity of Registers 2 Bytes 0x0000 to 0x007B
Byte Count 1 Byte 2 x N*
Register Value N* x 2 Bytes value
N = Quantity of Registers

Response

Function code 1 Byte 0x
Starting Address 2 Bytes 0x0000 to 0xFFFF
Quantity of Registers 2 Bytes 0x0000 to 0x007B
Error

Error code 1 Byte 0x
Exception code 1 Bytes 01 or 02 or 03 or 04
Here is an example of a request to write two registers starting at 2 to 00 0A and 01 02 hex:

Request Response
Field Name (hex) Field Name (hex)
Function 10 Function 10
Register Address Hi 00 Register Address Hi 00
Register Address Low 01 Register Address Low 01
Quantity of Registers Hi 00 Quantity of Registers Hi 00
Quantity of Registers Lo 02 Quantity of Registers Lo 02
Register Value Hi 00
Register Value Low 0A
Register Value Hi 01
Register Value Low 02
2.1.7 Exception Responses

When a client device sends a request to a server device it expects a normal response. One of four pos-
sible events can occur from the client’s query:

 (^) If the server device receives the request without a communication error, and can handle the query
normally, it returns a normal response.
 (^) If the server does not receive the request due to a communication error, no response is returned.
The client program will eventually process a timeout condition for the request.
 (^) If the server receives the request, but detects a communication error (parity, CRC...), no response is
returned. The client program will eventually process a timeout condition for the request.
 (^) If the server receives the request without a communication error, but cannot handle it (for example, if
the request is to read a non –existent output or register), the server will return an exception response
informing the client of the nature of the error.
The exception response message has two fields that differentiate it from a normal response:
Function Code Field: In a normal response, the server echoes the function code of the original
request in the function code field of the response. All function codes have a most – significant bit (MSB)
of 0 (their values are all below 80 hex). In an exception response, the server sets the MSB of the func-
tion code to 1. This makes the function code value in an exception response exactly 80 hex higher than
the value would be for a normal response.
With the function code’s MSB set, the client‘s application program can recognize the exception res-
ponse and can examine the data field for the exception code.
Data Field: In a normal response, the server may return data or statistics in the data field (any informa-
tion that was requested in the request). In an exception response, the server returns an exception code
in the data field. This defines the server condition that caused the exception.
The exception codes are listed:
MODBUS Exception Codes
Code Name Meaning
01 ILLEGAL FUNCTION The function code received in the query is not an allowable
action for the server. This may be because the function code
is only applicable to newer devices, and was not implemented
in the unit selected. It could also indicate that the server is in
the wrong state to process a request of this type, for example
because it is un-configured and is being asked to return regis-
ter values.

MODBUS Exception Codes

02 ILLEGAL DATA ADDRESS The data address received in the query is not an allowable
address for the server. More specifically, the combination of
reference number and transfer length is invalid. For a controller
with 100 registers, the PDU addresses the first register as 0,
and the last one as 99. If a request is submitted with a starting
register address of 96 and a quantity of registers of 4, then
this request will successfully operate (address -wise at least)
on registers 96, 97, 98, 99. If a request is submitted with a
starting register address of 96 and a quantity of registers of 5,
then this request will fail with Exception Code 0x02 “Illegal Data
Address” since it attempts to operate on registers 96, 97, 98,
99 and 100, and there is no register with address 100.
03 ILLEGAL DATA VALUE A value contained in the query data field is not an allowable
value for server. This indicates a fault in the structure of the
remainder of a complex request, such as that the implied
length is incorrect. It specifically does NOT mean that a data
item submitted for storage in a register has a value outside the
expectation of the application program, since the MODBUS
protocol is unaware of the significance of any particular value of
any particular register.
04 SERVER DEVICE FAILURE An unrecoverable error occurred while the server was attemp-
ting to perform the requested action.
05 ACKNOW LEDGE Specialized use in conjunction with programming commands.
06 SERVER DEVICE BUSY Specialized use in conjunction with programming commands.
The server is engaged in processing a long – duration program
command. The client should retransmit the message later
when the server is free.
08 MEMORY PARITY ERROR Specialized use in conjunction with function codes 20 and 21
and reference type 6, to indicate that the extended file area
failed to pass a consistency check. The server attempted to
read record file, but detected a parity error in the memory. The
client can retry the request, but service may be required on the
server device.
0A GATEWAY PATH UNAVAILABLE Specialized use in conjunction with gateways, indicates that
the gateway was unable to allocate an internal communication
path from the input port to the output port for processing the
request. Usually means that the gateway is misconfigured or
overloaded.
0B GATEWAY PATH UNAVAILABLE Specialized use in conjunction with gateways, indicates that no
response was obtained from the target device. Usually means
that the device is not present on the network.

2.2 Data Link Layer
2.2.1 Overview

The MODBUS TCP protocol is used in this interface.

MODBUS application layer

MODBUS on TCP
TCP
IP
Ethernet II / 802.
Ethernet
physical layer
Abb. 3: MODBUS Protocols and ISO/OSI Model

Layer ISO/OSI Layer
7 Application MODBUS / TCP
6 Presentation MODBUS / TCP
5 Session MODBUS / TCP
4 Transport TCP
3 Network IP
2 Data Link IEEE 802.3 (Ethernet)
1 Physical IEEE 802.3 (Ethernet)
2.3 Physical Layer
2.3.1 Ethernet port

A electrical interface in accordance with IEEE 802.3 standard is used for the interface. A RJ45 connec-
tor is used for connection.

2.3.2 Electrical interface

Abb. 4: As example here the PLENTICORE G3 Smart Communication Board - Interface

Item Designation Explanation
1 Ethernet connection (RJ45) RJ45 max. 100 Mbit (LAN connection for linking to a router, for example)
3. MODBUS Register table
3.1 TCP-Port and Unit-ID
To access the inverter via MODBUS / TCP, the following TCP-Port and MODBUS-Unit-ID are used as
default values:

TCP-Port 1502 (dec)
Unit-ID^1 71 (dec)
(^1) The Unit-ID is modifiable.

3.2 Query the operating data
Addr
(hex)
Addr
(dec)
Description Unit Format N1) Access Function
Code
0x02 2 MODBUS Enable - Bool 1 R/W 0x03/0x
0x04 4 MODBUS Unit-ID - U16 1 R/W 0x03/0x
0x05 5 MODBUS Byte Order Note^7 - U16 1 R/W 0x03/0x
0x06 6 Inverter article number - String 8 RO 0x
0x0E 14 Inverter serial number - String 8 RO 0x
0x1E 30 Number of bidirectional converter - U16 1 RO 0x
0x20 32 Number of AC phases - U16 1 RO 0x
0x22 34 Number of PV strings - U16 1 RO 0x
0x24 36 Hardware-Version - U16 2 RO 0x
0x26 38 Software-Version Maincontroller (MC) - String 8 RO 0x
0x2E 46 Software-Version IO-Controller (IOC) - String 8 RO 0x
0x36 54 Power-ID - U32 2 RO 0x
0x38 56 Inverter state^2 - U32 2 RO 0x
0x3A 58 Overall software version (UI / SW) - String 13 RO 0x
0x62 98 Temperature of controller PCB °C Float 2 RO 0x
0x64 100 Total DC power W Float 2 RO 0x
0x68 104 State of energy manager^3 - U32 2 RO 0x
0x6A 106 Home own consumption from battery^8 W Float 2 RO 0x
0x6C 108 Home own consumption from grid^8 W Float 2 RO 0x
0x6E 110 Total home consumption Battery^8 Wh Float 2 RO 0x
0x70 112 Total home consumption Grid^8 Wh Float 2 RO 0x
0x72 114 Total home consumption PV^8 Wh Float 2 RO 0x
0x74 116 Home own consumption from PV^8 W Float 2 RO 0x
0x76 118 Total home consumption^8 Wh Float 2 RO 0x
0x78 120 Isolation resistance Ohm Float 2 RO 0x
0x7A 122 Power limit from EVU % Float 2 RO 0x
0x7C 124 Total home consumption rate % Float 2 RO 0x
Addr
(hex)

Addr
(dec)
Description Unit Format N1) Access Function
Addr
(hex)

Addr
(dec)
Description Unit Format N1) Access Function
Code
0xFC 252 Total active power (powermeter)
Sensor position 1 (home consumption):
(+) House consumption, (-) generation
Sensor position 2 (grid connection):
(+) Power supply, (-) feed-in

W Float 2 RO 0x
0xFE 254 Total reactive power (powermeter)
Sensor position 2 (grid connection):
(+) Power supply, (-) feed-in
Sensor position 1 (home consumption):
(+) House consumption, (-) generation

Var Float 2 RO 0x
0x100 256 Total apparent power (powermeter)
Sensor position 2 (grid connection):
(+) Power supply, (-) feed-in
Sensor position 1 (home consumption):
(+) House consumption, (-) generation

VA Float 2 RO 0x
0x102 258 Current DC1 A Float 2 RO 0x
0x104 260 Power DC1 W Float 2 RO 0x
0x10A 266 Voltage DC1 V Float 2 RO 0x
0x10C 268 Current DC2 A Float 2 RO 0x
0x10E 270 Power DC2 W Float 2 RO 0x
0x114 276 Voltage DC2 V Float 2 RO 0x
0x116 278 Current DC3 A Float 2 RO 0x
0x118 280 Power DC3 W Float 2 RO 0x
0x11E 286 Voltage DC3 V Float 2 RO 0x
0x140 320 Total yield Wh Float 2 RO 0x
0x142 322 Daily yield Wh Float 2 RO 0x
0x144 324 Yearly yield Wh Float 2 RO 0x
0x146 326 Monthly yield Wh Float 2 RO 0x
0x180 384 Inverter network name - String 32 RO 0x
0x1A0 416 IP enable - U16 1 RO 0x
0x1A2 418 Manual IP / Auto-IP - U16 1 RO 0x
0x1A4 420 IP-address - String 8 RO 0x
0x1AC 428 IP-subnetmask - String 8 RO 0x
0x1B4 436 IP-gateway - String 8 RO 0x
0x1BC 444 IP-auto-DNS - U16 1 RO 0x
0x1BE 446 IP-DNS1 - String 8 RO 0x
0x1C6 454 IP-DNS2 - String 8 RO 0x
0x200 512 Battery gross capacity Ah U32 2 RO 0x
0x202 514 Battery actual SOC % U16 1 RO 0x
0x203 515 Firmware Maincontroller (MC) - U32 2 RO 0x
0x205 517 Battery Manufacturer - String 8 RO 0x

Addr
(hex)

Addr
(dec)
Description Unit Format N1) Access Function

Introduction
1.1 Disclaimer of liability
1.2 Intended Use
1.3 Target group
1.4 Safety Information
1.5 MODBUS Protocol
1.6 Data Formats
MODBUS protocol description
2.1 Application Layer
2.2 Data Link Layer
2.3 Physical Layer
MODBUS Register table
3.1 TCP-Port and Unit-ID
3.2 Query the operating data
3.3 Active power and reactive power control
3.4 External battery management
3.5 Battery limitation
SunSpec Interface
4.1 Overview
4.2 Implemented SunSpec Models
4.3 Startaddresses
0x90 144 Worktime s Float 2 RO 0x Code
0x96 150 Actual cos φ - Float 2 RO 0x
0x98 152 Grid frequency Hz Float 2 RO 0x
0x9A 154 Current Phase 1 A Float 2 RO 0x
0x9C 156 Active power Phase 1 W Float 2 RO 0x
0x9E 158 Voltage Phase 1 V Float 2 RO 0x
0xA0 160 Current Phase 2 A Float 2 RO 0x
0xA2 162 Active power Phase 2 W Float 2 RO 0x
0xA4 164 Voltage Phase 2 V Float 2 RO 0x
0xA6 166 Current Phase 3 A Float 2 RO 0x
0xA8 168 Active power Phase 3 W Float 2 RO 0x
0xAA 170 Voltage Phase 3 V Float 2 RO 0x
0xAC 172 Total AC active power W Float 2 RO 0x
0xAE 174 Total AC reactive power Var Float 2 RO 0x
0xB2 178 Total AC apparent power VA Float 2 RO 0x
0xBE 190 Battery charge current A Float 2 RO 0x
0xC2 194 Number of battery cycles - Float 2 RO 0x
0xC8 200 Actual battery charge (-) / discharge (+) current A Float 2 RO 0x
0xCA 202 PSSB fuse state^5 - Float 2 RO 0x
0xD0 208 Battery ready flag - Float 2 RO 0x
0xD2 210 Act. state of charge % Float 2 RO 0x
0xD6 214 Battery temperature °C Float 2 RO 0x
0xD8 216 Battery voltage V Float 2 RO 0x
0xDA 218 Cos φ (powermeter) - Float 2 RO 0x
0xDC 220 Frequency (powermeter) Hz Float 2 RO 0x
0xDE 222 Current phase 1 (powermeter) A Float 2 RO 0x
0xE0 224 Active power phase 1 (powermeter) W Float 2 RO 0x
0xE2 226 Reactive power phase 1 (powermeter) Var Float 2 RO 0x
0xE4 228 Apparent power phase 1 (powermeter) VA Float 2 RO 0x
0xE6 230 Voltage phase 1 (powermeter) V Float 2 RO 0x
0xE8 232 Current phase 2 (powermeter) A Float 2 RO 0x
0xEA 234 Active power phase 2 (powermeter) W Float 2 RO 0x
0xEC 236 Reactive power phase 2 (powermeter) Var Float 2 RO 0x
0xEE 238 Apparent power phase 2 (powermeter) VA Float 2 RO 0x
0xF0 240 Voltage phase 2 (powermeter) V Float 2 RO 0x
0xF2 242 Current phase 3 (powermeter) A Float 2 RO 0x
0xF4 244 Active power phase 3 (powermeter) W Float 2 RO 0x
0xF6 246 Reactive power phase 3 (powermeter) Var Float 2 RO 0x
0xF8 248 Apparent power phase 3 (powermeter) VA Float 2 RO 0x
0xFA 250 Voltage phase 3 (powermeter) V Float 2 RO 0x
0x20D 525 Battery Model ID - U32 2 RO 0x Code
0x20F 527 Battery Serial Number - U32 2 RO 0x
0x211 529 Work Capacity Wh U32 2 RO 0x
0x213 531 Inverter Max Power W U16 1 RO 0x
0x214 532 Inverter Max Power Scale Factor^4 - - 1 RO 0x
0x215 533 Active Power Setpoint % U16 1 RW 0x03/0x
0x217 535 Inverter Manufacturer - String 16 RO 0x
0x22F 559 Inverter Serial Number - String 16 RO 0x
0x23F 575 Inverter Generation Power (actual) W S16 1 RO 0x
0x240 576 Power Scale Factor^4 - - 1 RO 0x
0x241 577 Generation Energy Wh U32 2 RO 0x
0x243 579 Energy Scale Factor^4 - - 1 RO 0x
0x246 582 Actual battery charge/discharge power W S16 1 RO 0x
0x247 583 Reactive Power Setpoint % S16 1 RW 0x03/0x
0x249 585 Delta-cos φ Setpoint - S16 1 RW 0x03/0x
0x24A 586 Battery Firmware - U32 2 RO 0x
0x24C 588 Battery Type^6 - U16 1 RO 0x
0x260 608 I/O-Board, Switched Output 1 - U16 1 RW 0x03/0x
0x261 609 I/O-Board, Switched Output 2 - U16 1 RW 0x03/0x
0x262 610 I/O-Board, Switched Output 3 - U16 1 RW 0x03/0x
0x263 611 I/O-Board, Switched Output 4 - U16 1 RW 0x03/0x
0x300 768 Productname (e.g. PLENTICORE plus) - String 32 RO 0x
0x320 800 Power class (e.g. 10) - String 32 RO 0x
0x416 1046 Total DC charge energy (DC-side to battery) Wh Float 2 RO 0x
0x418 1048 Total DC discharge energy (DC-side from battery) Wh Float 2 RO 0x
0x41A 1050 Total AC charge energy (AC-side to battery) Wh Float 2 RO 0x
0x41C 1052 Total AC discharge energy (battery to grid) Wh Float 2 RO 0x
0x41E 1054 Total AC charge energy (grid to battery) Wh Float 2 RO 0x
0x420 1056 Total DC PV energy (sum of all PV inputs) Wh Float 2 RO 0x
0x422 1058 Total DC energy from PV1 Wh Float 2 RO 0x
0x424 1060 Total DC energy from PV2 Wh Float 2 RO 0x
0x426 1062 Total DC energy from PV3 Wh Float 2 RO 0x
0x428 1064 Total energy AC-side to grid Wh Float 2 RO 0x
0x42A 1066 Total DC power (sum of all PV inputs) W Float 2 RO 0x
0x9D9A 40346 Total Real Energy Exported^9 Wh U32 2 RO 0x
0x9DA2 40354 Total Real Energy Imported^9 Wh U32 2 RO 0x
Notes:

(^1) N = Quantity of Registers
(^2) Inverter States
0 Off
1 Init
2 IsoMeas
3 GridCheck
4 StartUp
5 -
6 FeedIn
7 Throttled
8 ExtSwitchOff
9 Update
10 Standby
11 GridSync
12 GridPreCheck
13 GridSwitchOff
14 Overheating
15 Shutdown
16 ImproperDcVoltage
17 ESB
18 Unknown
(^3) States of energy manager (internal energy flow)
0x00 Idle
0x01 n/a
0x02 Emergency Battery Charge
0x04 n/a
0x08 Winter Mode Step 1
0x10 Winter Mode Step 2
(^4) Scale factors: As an alternative to floating point format, values are represented by integer values with
a signed scale factor applied. The scale factor explicitly shifts the decimal point to the left (negative
value) or the right (positive value). Scale factors are 16 bit two’s complement integer, the signed range is
-10 ... 10.

(^5) PSSB-fuse-state
0x00 Fuse fail
0x01 Fuse ok
0xFF Unchecked
(^6) Battery type
0x0000 No battery (PV-Functionality)
0x0002 PIKO Battery Li
0x0004 BYD
0x0008 BMZ
0x0010 AXIstorage Li SH
0x0040 LG
0x0200 Pyontech Force H
0x0400 AXIstorage Li SV
0x1000 Dyness Tower / TowerPro
0x2000 VARTA.wall
0x4000 ZYC
(^7) MODBUS Byte Order, important for read or write accesses to float-formatted registers
0x00 Little-endian (CDAB)
0x01 Big-endian (ABCD)
(^8) Explanations of the energy flow
Home own consumption from battery/grid/PV: The actual power in Watt consumed by home and
provided from battery, grid, or PV.
Total home consumption battery/grid/PV: The total amount of energy in Watthours consumed by
home and provided from battery, grid, or PV.
Total home consumption: Sum of all energy consumed by home and provided from battery, grid and
P V.
(^9) Valid from version
PLENTICORE G2: SW 02.12.xxxxx
PLENTICORE G3: SW 3.04.01.xxxxx
PLENTICORE MP G3: SW 3.05.00.xxxxx

3.3 Active power and reactive power control
These registers are only writeable and their parameters are not stored in the long-term storage memory
of the inverter. That means the inverter will discard these settings when it powers on or resets.

Addr(Hex) Addr(DEC) Description Format *N Function
0x215 533 Active power setpoint (%), range:1..100 U16 1 0x06
0x247 583 Reactive power setpoint (%), range: -100...0...100 S16 1 0x06
0x249 585 Delta cos φ setpoint^1 range: -32768...0...32767 S16 1 0x06
0x340 832 Low-Priority Active power setpoint (W), range 0...65535^2 U16 1 0x06
0x341 833 Low-Priority Active power setpoint scale factor2, 3 S8 1 0x03
*N = Quantity of Registers

Note:

(^1) The inverters can contribute to grid management by feeding in with a fixed cos φ. The displacement
factor cos φ is an unsigned factor that is nonetheless characterized by the suffix „underexcited“ or
„overexcited“. However, a „delta cos φ“ is transmitted here to the inverter, whereby a negative value
corresponds to underexcited operation and a positive value corresponds to overexcited operation.
The value range -1.0...+1.0 is shown for the transmission of the desired value to the value range
-32768...+32767 of a 16 bit whole number (signed short) with a suffix.
Example:
Setting Delta cos φ Reactive power
0 0 cos φ = 1.00
-1638 -0.05 cos φ = 0.95 underexcited
+3276 +0.10 cos φ = 0.90 overexcited
The maximum setting range is -26214...26214 (corresponds to a max. cos φ of 0.80). The setting
range actually realizable by the inverter is found in the data sheet of the inverter.
(^2) Supported with SW-Version 1.30 or greater
(^3) Scale factor:
As an alternative to floating point format, the low-priority Active Power Setpoint is represented by an
integer value with a signed scale factor applied. The scale factor explicitly shifts the decimal point to
the left (negative value) or the right (positive value). Scale factors are 8 bit two’s compliment integer, the
signed range is -10 ... 10.
I.e., a setpoint of 10kW is represented by a value of 10,000 and a scale factor of 0. A setpoint of 200kW
is represented by a value of 20000 and a scale factor of +1. The scale factor is read only and has to be
read first to get the factor used by the inverter. The desired setpoint has to be scaled with this factor:
Setpoint [W] = Register-Value 832 * (10 ^ Register-Value 833 )

3.4 External battery management
Addr
(hex)
Addr
(dec)
Description Unit Format N1) Access Function
Code
0x400 1024 Battery charge power (AC) setpoint Note1,6 W S16 1 RO 0x06
0x401 1025 Power Scale Factor Note2, 6 - S16 1 RO 0x03
0x402 1026 Battery charge power (AC) setpoint, absolute
Note1,6
W Float 2 RW 0x03/0x10
0x404 1028 Battery charge current (DC) setpoint, relative
Note 1,3,7
% Float 2 RW 0x03/0x10
0x406 1030 Battery charge power (AC) setpoint, relative
Note 1,3,6
% Float 2 RW 0x03/0x10
0x408 1032 Battery charge current (DC) setpoint, absolute
Note 1,7
A Float 2 RW 0x03/0x10
0x40A 1034 Battery charge power (DC) setpoint, absolute
Note 1,7
W Float 2 RW 0x03/0x10
0x40C 1036 Battery charge power (DC) setpoint, relative
Note 1,3,7
% Float 2 RW 0x03/0x10
0x40E 1038 Battery max. charge power limit, absolute W Float 2 RW 0x03/0x10
0x410 1040 Battery max. discharge power limit, absolute W Float 2 RW 0x03/0x10
0x412 1042 Minimum SOC % Float 2 RW 0x03/0x10
0x414 1044 Maximum SOC % Float 2 RW 0x03/0x10
0x42C 1068 Battery work capacity Wh Float 2 RO 0x03
0x42E 1070 Battery serial number - U32 2 RO 0x03
0x430 1072 Reserved - - 2 RO 0x03
0x432 1074 Reserved - - 2 RO 0x03
0x434 1076 Maximum charge power limit (read-out from
battery)
W Float 2 RO 0x03
0x436 1078 Maximum discharge power limit (read-out from
battery)
W Float 2 RO 0x03
0x438 1080 Battery management mode Note^4 - U8 1 RO 0x03
0x439 1081 reserved - - 1 RO 0x03
0x43A 1082 Installed sensor type Note^5 - U8 1 RO 0x03
Notes:

(^1) Negative values will charge the battery, positive values will discharge the battery.
(^2) Scale factor:
As an alternative to floating point format, the battery charge power setpoint is represented by an integer
value with a signed scale factor applied. The scale factor explicitly shifts the decimal point to the left
(negative value) or the right (positive value). Scale factors are 8 bit two’s compliment integer, the signed
range is -10 ... 10.
I.e., a setpoint of 10kW is represented by a value of 10,000 and a scale factor of 0. A setpoint of
200kW is represented by a value of 20000 and a scale factor of +1. The scale factor is read only and
has to be read first to get the factor used by the inverter. The desired setpoint has to be scaled with this
factor:
Setpoint [W] = Register-Value 1024 * (10 ^ Register-Value 1025)
(^3) In relation to the corresponding nominal values Inom or Pnom
(^4) Battery management modes
0x00 No external battery management
0x01 External battery management via digital I/O
0x02 External battery management via MODBUS protocol
(^5) Sensor types
0x00 SDM 630 (B+G E-Tech GmbH)
0x01 B-Control EM-300 LR (TQ Systems)
0x02 reserved
0x03 KOSTAL Smart Energy Meter (KOSTAL)
0xFF No sensor
(^6) PLENTICORE BI and PLENTICORE (MP) G3
(^7) Due to normative requirements for gradient limitation, access to this register is not possible
permanently in Denmark, nor in Austria for the gradient case (10 minutes).

3.5 Battery limitation
The battery limitation is available only for PLENTICORE G3 inverters from software version 03.05.xxxxx.

Addr
(hex)
Addr
(dec)
Description Unit Format N1) Access Function
Code
0x500 1280 Max battery charge power W Float 2 RW 0x03/0x10
0x502 1282 Max battery discharge power W Float 2 RW 0x03/0x10
0x504 1284 Max battery charge power (fallback case) W Float 2 RW 0x03/0x10
0x506 1286 Max battery discharge power (fallback case) W Float 2 RW 0x03/0x10
0x508 1288 Time until fallback (1 s U32 2 RW 0x03/0x10
As soon as register 0x500 or 0x502 has been written, they must continue to be written cyclically. If this
no longer happens, after a few seconds (time of register 0x508) the limits in register 0x504 and 0x506
become active.

Notes:

(^1) Value range: 30 .. 10800 seconds.

4. SunSpec Interface
4.1 Overview
Information in SunSpec is defined through a set of ‘Information Models’ representing functionality imple-
mented by devices or plants. SunSpec Alliance Interoperability Specifications describe these informa-
tion models, data exchange formats and communication protocols used in distributed energy resource
systems.

SunSpec information Models are defined using the SunSpec Model Definition XML (SMDX) encoding.
Please reference the SMDX file for the definitive version of any SunSpec Information Model, at
http://sunspec.org/download.

SunSpec information Models are communication protocol agnostic, but MODBUS is currently the most
popular transport protocol in use.

For further information refer to http://www.sunspec.org.

4.2 Implemented SunSpec Models
Currently the following SunSpec-Models are implemented:

Model-No Model-Name
1 Common
103 Three Phase Inverter
113 Three Phase Inverter, float
120 Nameplate
123 Immediate Controls
160 Multiple MPPT
2031 Wye-Connect Three Phase (abcn) Meter
802 Battery Base Model
65535 End Model
Notes:

(^1) Valid from version
PLENTICORE G2: SW 02.12.xxxxx
PLENTICORE G3: SW 3.04.01.xxxxx
PLENTICORE MP G3: SW 3.05.00.xxxxx

4.3 Startaddresses
SunSpec device definitions are constructed by concatenating a collection of SunSpec Information
Models, starting with the Common Model and ending with the End Model. Each Information Model is
uniquely identified and contains a well-known identifier and length. It is therefore recommended that the
implemented SunSpec structure is read automatically by browsing the structure until the End-Model is
reached.

The following table lists the implemented start addresses for convenience.

Model-No Startaddress (dec)
1 40003
103 40071
113 40123
120 40185
123 40213
160 40239
203 40309
802 40416
65535 40480
http://www.kostal-solar-electric.com

10/2025 - EN - DOC02308910-0016 - Technische Änderungen und Irrtümer vorbehalten.Aktuelle Informationen finden Sie unter http://www.kostal-solar-electric.com.