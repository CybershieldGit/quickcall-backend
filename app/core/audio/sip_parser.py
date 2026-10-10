"""
SIP Message Parser & Digest Authentication Builder
Supports SIP over WebSocket (RFC 7118) for Asterisk WebRTC
"""
import re
import uuid
import hashlib
from typing import Dict, Optional, Tuple


class SipMessage:
    def __init__(self, raw_text: str):
        self.raw_text = raw_text
        self.is_response = False
        self.is_request = False
        self.method = ""
        self.uri = ""
        self.status_code = 0
        self.reason_phrase = ""
        self.headers: Dict[str, str] = {}
        self.body = ""

        self._parse(raw_text)

    def _parse(self, text: str):
        # Normalize line endings to \n to reliably handle CRLF, LF, or mixed endings
        norm_text = text.replace("\r\n", "\n").replace("\r", "\n")
        parts = norm_text.split("\n\n", 1)
        if len(parts) == 2:
            head, self.body = parts
        else:
            head = norm_text
            self.body = ""

        lines = [line.strip() for line in head.split("\n") if line.strip()]
        if not lines:
            return

        start_line = lines[0]
        if start_line.startswith("SIP/2.0"):
            self.is_response = True
            tokens = start_line.split(" ", 2)
            if len(tokens) >= 2 and tokens[1].isdigit():
                self.status_code = int(tokens[1])
                self.reason_phrase = tokens[2] if len(tokens) > 2 else ""
        else:
            self.is_request = True
            tokens = start_line.split(" ", 2)
            if len(tokens) >= 2:
                self.method = tokens[0].upper()
                self.uri = tokens[1]

        # Parse headers (handling case-insensitivity)
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                header_name = k.strip().lower()
                self.headers[header_name] = v.strip()

    def get_header(self, name: str, default: str = "") -> str:
        # Common short headers in SIP
        short_names = {
            "to": "t",
            "from": "f",
            "call-id": "i",
            "contact": "m",
            "content-type": "c",
            "via": "v",
            "cseq": "cseq"
        }
        name_lower = name.lower()
        if name_lower in self.headers:
            return self.headers[name_lower]
        short = short_names.get(name_lower)
        if short and short in self.headers:
            return self.headers[short]
        return default


def parse_auth_challenge(auth_header: str) -> Dict[str, str]:
    """Parses WWW-Authenticate or Proxy-Authenticate header."""
    params = {}
    matches = re.findall(r'(\w+)="?([^",]+)"?', auth_header)
    for k, v in matches:
        params[k.lower()] = v
    return params


def compute_digest_response(
    username: str,
    password: str,
    realm: str,
    nonce: str,
    uri: str,
    method: str = "REGISTER",
    qop: Optional[str] = None,
    nc: str = "00000001",
    cnonce: Optional[str] = None
) -> str:
    """Calculates MD5 SIP Digest Authentication response (RFC 2617)."""
    ha1_str = f"{username}:{realm}:{password}"
    ha1 = hashlib.md5(ha1_str.encode("utf-8")).hexdigest()

    ha2_str = f"{method}:{uri}"
    ha2 = hashlib.md5(ha2_str.encode("utf-8")).hexdigest()

    if qop and "auth" in qop.lower():
        cnonce = cnonce or uuid.uuid4().hex[:8]
        response_str = f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}"
    else:
        response_str = f"{ha1}:{nonce}:{ha2}"

    return hashlib.md5(response_str.encode("utf-8")).hexdigest()


def build_auth_header(
    username: str,
    password: str,
    realm: str,
    nonce: str,
    uri: str,
    method: str = "REGISTER",
    algorithm: str = "MD5",
    qop: Optional[str] = None,
    opaque: Optional[str] = None,
    nc: str = "00000001",
    cnonce: Optional[str] = None
) -> str:
    cnonce = cnonce or uuid.uuid4().hex[:8]
    response = compute_digest_response(
        username, password, realm, nonce, uri, method=method, qop=qop, nc=nc, cnonce=cnonce
    )
    parts = [
        f'Digest algorithm={algorithm}',
        f'username="{username}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{uri}"',
        f'response="{response}"'
    ]
    if opaque:
        parts.append(f'opaque="{opaque}"')
    if qop:
        parts.append(f'qop={qop}')
        parts.append(f'cnonce="{cnonce}"')
        parts.append(f'nc={nc}')
    return ", ".join(parts)



def create_register_request(
    username: str,
    domain: str,
    port: int = 5000,
    call_id: Optional[str] = None,
    cseq: int = 1,
    from_tag: Optional[str] = None,
    auth_header: Optional[str] = None,
    expires: int = 300,
    ws_instance: Optional[str] = None
) -> Tuple[str, str, str]:
    """
    Builds a SIP REGISTER request for Asterisk WebRTC over WebSocket.
    Returns: (raw_sip_text, call_id, from_tag)
    """
    call_id = call_id or f"{uuid.uuid4().hex}@{domain}"
    from_tag = from_tag or uuid.uuid4().hex[:8]
    branch = f"z9hG4bK{uuid.uuid4().hex[:10]}"
    ws_instance = ws_instance or uuid.uuid4().hex[:12]

    uri = f"sip:{domain}:{port}"
    from_to_uri = f"<sip:{username}@{domain}>"

    headers = [
        f"REGISTER {uri} SIP/2.0",
        f"Via: SIP/2.0/WSS {ws_instance}.invalid;branch={branch}",
        f"Max-Forwards: 70",
        f"To: {from_to_uri}",
        f"From: {from_to_uri};tag={from_tag}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} REGISTER",
        f"Contact: <sip:{username}@{ws_instance}.invalid;transport=ws>;expires={expires}",
        f"Allow: INVITE,ACK,CANCEL,BYE,UPDATE,MESSAGE,OPTIONS,REFER,INFO,NOTIFY",
        f"Supported: path,gruu,outbound",
        f"User-Agent: QwickCall-VoiceAgent-WebRTC",
        f"Expires: {expires}"
    ]

    if auth_header:
        headers.append(f"Authorization: {auth_header}")

    headers.append("Content-Length: 0")
    headers.append("")
    headers.append("")

    return "\r\n".join(headers), call_id, from_tag


def create_options_200_ok(
    options_msg: SipMessage,
    username: str,
    host: str
) -> str:
    """Replies 200 OK to Asterisk qualify OPTIONS request to keep the endpoint reachable."""
    via = options_msg.get_header("via")
    from_h = options_msg.get_header("from")
    to_h = options_msg.get_header("to")
    call_id = options_msg.get_header("call-id")
    cseq = options_msg.get_header("cseq")
    if ";tag=" not in to_h:
        to_h = f"{to_h};tag={uuid.uuid4().hex[:8]}"

    headers = [
        "SIP/2.0 200 OK",
        f"Via: {via}",
        f"From: {from_h}",
        f"To: {to_h}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq}",
        f"Contact: <sip:{username}@{host};transport=ws>",
        "Allow: INVITE,ACK,CANCEL,BYE,UPDATE,MESSAGE,OPTIONS,REFER,INFO,NOTIFY",
        "Supported: replaces, timer",
        "Content-Length: 0",
        "",
        ""
    ]
    return "\r\n".join(headers)


def create_trying_response(
    invite_msg: SipMessage
) -> str:
    """Sends 100 Trying to Asterisk immediately to stop INVITE retransmissions without triggering ringback tone."""
    to_header = invite_msg.get_header("to")
    via_header = invite_msg.get_header("via")
    from_header = invite_msg.get_header("from")
    call_id = invite_msg.get_header("call-id")
    cseq = invite_msg.get_header("cseq")

    headers = [
        "SIP/2.0 100 Trying",
        f"Via: {via_header}",
        f"From: {from_header}",
        f"To: {to_header}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq}",
        "Content-Length: 0",
        "",
        ""
    ]
    return "\r\n".join(headers)


def create_ringing_response(
    invite_msg: SipMessage,
    to_tag: Optional[str] = None
) -> str:
    """Sends 180 Ringing to Asterisk immediately while WebRTC SDP is negotiating."""
    to_tag = to_tag or uuid.uuid4().hex[:8]
    to_header = invite_msg.get_header("to")
    if ";tag=" not in to_header:
        to_header = f"{to_header};tag={to_tag}"

    via_header = invite_msg.get_header("via")
    from_header = invite_msg.get_header("from")
    call_id = invite_msg.get_header("call-id")
    cseq = invite_msg.get_header("cseq")
    record_route = invite_msg.get_header("record-route")

    headers = [
        "SIP/2.0 180 Ringing",
        f"Via: {via_header}",
        f"From: {from_header}",
        f"To: {to_header}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq}"
    ]
    if record_route:
        headers.append(f"Record-Route: {record_route}")
    headers.extend([
        "Content-Length: 0",
        "",
        ""
    ])
    return "\r\n".join(headers)


def create_200_ok_response(
    invite_msg: SipMessage,
    sdp_answer: str,
    to_tag: Optional[str] = None,
    username: str = "08239",
    host: str = "kenyavoice.rpdigitalphone.com"
) -> str:
    """Creates a 200 OK response with SDP Answer to accept incoming INVITE."""
    to_header = invite_msg.get_header("to")
    if ";tag=" not in to_header:
        to_tag = to_tag or uuid.uuid4().hex[:8]
        to_header = f"{to_header};tag={to_tag}"
    elif to_tag and ";tag=" in to_header:
        base_to = to_header.split(";tag=")[0]
        to_header = f"{base_to};tag={to_tag}"

    via_header = invite_msg.get_header("via")
    from_header = invite_msg.get_header("from")
    call_id = invite_msg.get_header("call-id")
    cseq = invite_msg.get_header("cseq")
    record_route = invite_msg.get_header("record-route")

    sdp_bytes = sdp_answer.encode("utf-8")

    headers = [
        "SIP/2.0 200 OK",
        f"Via: {via_header}",
        f"From: {from_header}",
        f"To: {to_header}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq}"
    ]
    if record_route:
        headers.append(f"Record-Route: {record_route}")
    headers.extend([
        f"Contact: <sip:{username}@{host};transport=ws>",
        "Allow: INVITE,ACK,CANCEL,BYE,UPDATE,MESSAGE,OPTIONS,REFER,INFO,NOTIFY",
        "Supported: replaces, timer",
        "Content-Type: application/sdp",
        f"Content-Length: {len(sdp_bytes)}",
        "",
        sdp_answer
    ])
    return "\r\n".join(headers)


def create_bye_request(
    call_id: str,
    from_uri: str,
    to_uri: str,
    request_uri: Optional[str] = None,
    cseq: int = 100,
    route: Optional[str] = None
) -> str:
    """Builds a standard RFC 3261 compliant SIP BYE request to terminate an active call session."""
    branch = f"z9hG4bK{uuid.uuid4().hex[:10]}"
    req_uri = request_uri or to_uri
    if "<" in req_uri and ">" in req_uri:
        target_uri = req_uri.split("<")[1].split(">")[0]
    else:
        target_uri = req_uri.strip()
    target_uri = target_uri.split(";tag=")[0].strip()

    headers = [
        f"BYE {target_uri} SIP/2.0",
        f"Via: SIP/2.0/WSS {uuid.uuid4().hex}.invalid;branch={branch}",
        "Max-Forwards: 70",
        f"From: {from_uri}",
        f"To: {to_uri}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} BYE"
    ]
    if route:
        headers.append(f"Route: {route}")
    headers.extend([
        "Content-Length: 0",
        "",
        ""
    ])
    return "\r\n".join(headers)


def create_refer_request(
    call_id: str,
    from_uri: str,
    to_uri: str,
    refer_to_uri: str,
    contact_uri: str = "<sip:08239@kenyavoice.rpdigitalphone.com;transport=ws>",
    request_uri: Optional[str] = None,
    cseq: int = 101,
    route: Optional[str] = None
) -> str:
    """Builds a standard RFC 3515 compliant SIP REFER request for live call transfer."""
    branch = f"z9hG4bK{uuid.uuid4().hex[:10]}"
    req_uri = request_uri or to_uri
    if "<" in req_uri and ">" in req_uri:
        target_uri = req_uri.split("<")[1].split(">")[0]
    else:
        target_uri = req_uri.strip()

    referred_by = from_uri.split(";")[0] if ";" in from_uri else from_uri

    headers = [
        f"REFER {target_uri} SIP/2.0",
        f"Via: SIP/2.0/WSS {uuid.uuid4().hex}.invalid;branch={branch}",
        "Max-Forwards: 70",
        f"From: {from_uri}",
        f"To: {to_uri}",
        f"Contact: {contact_uri}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} REFER",
        f"Refer-To: {refer_to_uri}",
        f"Referred-By: {referred_by}"
    ]
    if route:
        headers.append(f"Route: {route}")
    headers.extend([
        "Content-Length: 0",
        "",
        ""
    ])
    return "\r\n".join(headers)


def create_invite_request(
    from_user: str,
    to_number: str,
    domain: str,
    port: int,
    sdp_offer: str,
    call_id: Optional[str] = None,
    cseq: int = 1,
    from_tag: Optional[str] = None,
    auth_header: Optional[str] = None,
    route: Optional[str] = None
) -> Tuple[str, str, str]:
    """
    Builds a SIP INVITE request for dialing an external number over WebRTC Asterisk WebSocket.
    Returns: (raw_sip_text, call_id, from_tag)
    """
    call_id = call_id or f"{uuid.uuid4().hex}@{domain}"
    from_tag = from_tag or uuid.uuid4().hex[:8]
    branch = f"z9hG4bK{uuid.uuid4().hex[:10]}"
    ws_instance = uuid.uuid4().hex

    request_uri = f"sip:{to_number}@{domain}"
    from_uri = f"<sip:{from_user}@{domain}>;tag={from_tag}"
    to_uri = f"<sip:{to_number}@{domain}>"

    sdp_bytes = sdp_offer.encode("utf-8")

    headers = [
        f"INVITE {request_uri} SIP/2.0",
        f"Via: SIP/2.0/WSS {ws_instance}.invalid;branch={branch}",
        f"Max-Forwards: 70",
        f"To: {to_uri}",
        f"From: {from_uri}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} INVITE",
        f"Contact: <sip:{from_user}@{ws_instance}.invalid;transport=ws>",
        f"Allow: INVITE,ACK,CANCEL,BYE,UPDATE,MESSAGE,OPTIONS,REFER,INFO,NOTIFY",
        f"Supported: replaces, timer",
        f"User-Agent: QwickCall-VoiceAgent-WebRTC",
        f"Content-Type: application/sdp",
        f"Content-Length: {len(sdp_bytes)}"
    ]
    if route:
        headers.append(f"Route: {route}")
    if auth_header:
        headers.append(f"Authorization: {auth_header}")

    headers.append("")
    headers.append(sdp_offer)

    return "\r\n".join(headers), call_id, from_tag


def create_ack_request(
    from_uri: str,
    to_uri: str,
    request_uri: str,
    call_id: str,
    cseq_num: int = 1,
    route: Optional[str] = None
) -> str:
    """Builds a SIP ACK request to confirm receipt of 200 OK for an INVITE dialog."""
    branch = f"z9hG4bK{uuid.uuid4().hex[:10]}"
    ws_instance = uuid.uuid4().hex
    target_uri = request_uri.split("<")[1].split(">")[0] if "<" in request_uri else request_uri
    headers = [
        f"ACK {target_uri} SIP/2.0",
        f"Via: SIP/2.0/WSS {ws_instance}.invalid;branch={branch}",
        f"Max-Forwards: 70",
        f"To: {to_uri}",
        f"From: {from_uri}",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq_num} ACK"
    ]
    if route:
        headers.append(f"Route: {route}")
    headers.extend([
        "Content-Length: 0",
        "",
        ""
    ])
    return "\r\n".join(headers)


