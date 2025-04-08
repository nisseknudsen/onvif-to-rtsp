import html
import logging
import time
from datetime import datetime, timedelta

import make87
from make87 import ProviderNotAvailable
from make87_messages.core.header_pb2 import Header
from make87_messages.primitive.bool_pb2 import Bool
from make87_messages.transport.auth_pb2 import DigestAuth, DigestAlgorithm
from make87_messages.transport.endpoint_pb2 import Endpoint
from make87_messages.transport.rtsp_pb2 import RTSPRequest, RTSPMethod
from make87_messages.video.any_pb2 import FrameAny
from make87_messages.video.frame_av1_pb2 import FrameAV1
from make87_messages.video.frame_h264_pb2 import FrameH264
from make87_messages.video.frame_h265_pb2 import FrameH265
from onvif import ONVIFCamera
from urllib.parse import urlparse, urlunparse, parse_qs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def rtsp_uri_to_request_message(rtsp_uri: str, username: str, password: str) -> RTSPRequest:
    # Unescape HTML entities (i.e., convert &amp; to &)
    rtsp_uri = html.unescape(rtsp_uri)

    # Parse the URL into components
    parsed = urlparse(rtsp_uri)

    base_header = Header()
    base_header.timestamp.GetCurrentTime()
    base_header.entity_path = f"/rtsp_request{parsed.path}"

    # Create the Endpoint message using parsed values.
    endpoint_msg = Endpoint(
        header=base_header,  # Populate header fields if needed.
        protocol=parsed.scheme,  # "rtsp"
        host=parsed.hostname,  # "localhost"
        port=parsed.port,  # 8080
        path=parsed.path,  # "/Streaming/tracks/401/"
        query_params={k: v[0] for k, v in parse_qs(parsed.query).items()},
    )

    # Create a DigestAuth message with placeholder values.
    digest_auth_msg = DigestAuth(
        header=base_header,  # Populate header fields if needed.
        username=username,  # "admin"
        password=password,  # "password"
        algorithm=DigestAlgorithm.MD5,  # Use an enum value from DigestAlgorithm.
    )

    # Build the RTSPRequest message.
    # For storing a stream with ffmpeg, typically the PLAY method is used.
    rtsp_request_msg = RTSPRequest(
        header=base_header,  # Top-level header; set fields as required.
        endpoint=endpoint_msg,
        method=RTSPMethod.PLAY,
        digest_auth=digest_auth_msg,  # Using DigestAuth as the chosen auth option.
    )

    return rtsp_request_msg


def parse_url(url):
    parsed = urlparse(url)
    protocol = parsed.scheme
    ip = parsed.hostname
    port = parsed.port  # Will be None if not specified in the URL
    url_suffix = parsed.path  # The part after the IP and port

    return protocol, ip, port, url_suffix


def inject_rtsp_auth(uri: str, username: str, password: str) -> str:
    parsed = urlparse(uri)

    netloc_with_auth = f"{username}:{password}@{parsed.hostname}"
    if parsed.port:
        netloc_with_auth += f":{parsed.port}"

    return urlunparse(
        (
            parsed.scheme,
            netloc_with_auth,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def main():
    make87.initialize()
    rtsp_stream_endpoint = make87.get_requester(
        name="RTSP_STREAM", requester_message_type=RTSPRequest, provider_message_type=Bool
    )

    onvif_url = make87.resolve_peripheral_name("ONVIF_DEVICE")
    username, password = (
        make87.get_config_value("ONVIF_USERNAME"),
        make87.get_config_value("ONVIF_PASSWORD"),
    )
    profile_indices = make87.get_config_value(
        "PROFILE_INDEX", default="0", decode=lambda s: [int(x.strip()) for x in s.split(",") if x.strip()]
    )
    profile_indices = [0] if not profile_indices else profile_indices

    protocol, ip, port, url_suffix = parse_url(onvif_url)

    camera = ONVIFCamera(host=ip, port=port, user=username, passwd=password)

    # --- Get the streaming URI via the Media service ---
    # Create the media service client.
    media_service = camera.create_media_service()

    # Retrieve available profiles (video configurations)
    profiles = media_service.GetProfiles()
    if len(profiles) < max(profile_indices) + 1:
        raise Exception(f"No profile with index {max(profile_indices)} available.")

    while True:
        for profile_index in profile_indices:
            default_profile = profiles[profile_index]

            logging.debug("Selected Profile:")
            logging.debug(default_profile)

            # Create a request to get the stream URI.
            stream_req = media_service.create_type("GetStreamUri")
            stream_req.ProfileToken = default_profile.token
            stream_req.StreamSetup = {
                "Stream": "RTP-Unicast",
                "Transport": {"Protocol": "RTSP"},
            }

            stream_uri = media_service.GetStreamUri(stream_req).Uri
            logging.info(f"Stream URI: {stream_uri}")

            _, _, _, entity_path = parse_url(url=stream_uri)
            rtsp_request = rtsp_uri_to_request_message(
                rtsp_uri=stream_uri,
                username=username,
                password=password,
            )

            try:
                rtsp_stream_endpoint.request(rtsp_request, timeout=10.0)
            except ProviderNotAvailable:
                logging.error(f"RTSP stream provider did not return within specified timeout of 10s.")
                continue
        else:
            logging.debug("Announced all streams. Repeating in 1 second.")
            time.sleep(1)


if __name__ == "__main__":
    main()
