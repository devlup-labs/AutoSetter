"""
autosetter.cli
==============
Command-line interface for AutoSetter.

    autosetter statement.png [options]

The CLI:
1. Opens an SSH tunnel to the Ollama GPU server when `--ssh` / AUTOSETTER_SSH_HOST is set.
2. Asks for the Polygon API key and secret (or reads POLYGON_API_KEY /
   POLYGON_SECRET) and checks them against Polygon before the long run starts.
3. Runs the pipeline (`autosetter.runner.generate_from_image`): extraction,
   existing-problem check (k=1, cosine > 0.80 prints the link and stops),
   generation, validation and packaging.
4. Pushes the verified package to Polygon with `autosetter.polygon.publish_package`.

Exit codes are listed in `main`.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional

from autosetter.config import (
    DEFAULT_NUM_TESTS,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OUT_DIR,
    DEFAULT_TEXT_MODEL,
    DEFAULT_VISION_MODEL,
    POLYGON_API_KEY,
    POLYGON_SECRET,
    SIMILARITY_ENABLED,
    SIMILARITY_THRESHOLD,
    SIMILARITY_TOP_K,
    SSH_HOST,
    SSH_KEY,
    SSH_LOCAL_PORT,
    SSH_PORT,
    SSH_REMOTE_PORT,
)
from autosetter.polygon import (
    PolygonAPIError,
    PolygonClient,
    prompt_credentials,
    publish_package,
)
from autosetter.remote import SSHTunnel, SSHTunnelError
from autosetter.runner import (
    AutoSetterError,
    AutoSetupError,
    PipelineResult,
    generate_from_image,
)

__all__ = [
    "AutoSetterError",
    "AutoSetupError",
    "PipelineResult",
    "build_arg_parser",
    "generate_from_image",
    "main",
]

EXIT_RELEASED = 0
EXIT_FAILED = 1
EXIT_NOT_RELEASABLE = 2
EXIT_EXISTING_PROBLEM = 3
EXIT_POLYGON_FAILED = 4

MAX_CREDENTIAL_ATTEMPTS = 3


@dataclass
class PolygonTarget:
    """Verified credentials and where to push the package."""

    client: PolygonClient
    problem_id: Optional[int] = None
    name: Optional[str] = None


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line interface parser."""
    parser = argparse.ArgumentParser(
        prog="autosetter",
        description=(
            "Turn a competitive programming statement (image or PDF) into a verified "
            "Polygon package and push it to Codeforces Polygon."
        ),
    )
    parser.add_argument(
        "image_path",
        type=str,
        help="Local path to problem statement image (.png/.jpg/.jpeg) or document (.pdf).",
    )

    models = parser.add_argument_group("models")
    models.add_argument(
        "--vision-model",
        type=str,
        default=DEFAULT_VISION_MODEL,
        help=f"Ollama vision model name (default: {DEFAULT_VISION_MODEL}).",
    )
    models.add_argument(
        "--text-model",
        type=str,
        default=DEFAULT_TEXT_MODEL,
        help=f"Ollama text model name (default: {DEFAULT_TEXT_MODEL}).",
    )
    models.add_argument(
        "--host",
        type=str,
        default=DEFAULT_OLLAMA_HOST,
        help=f"Ollama server URL (default: {DEFAULT_OLLAMA_HOST}). Ignored with --ssh.",
    )

    ssh = parser.add_argument_group("SSH tunnel to the Ollama GPU server")
    ssh.add_argument(
        "--ssh",
        dest="ssh_host",
        type=str,
        default=SSH_HOST,
        metavar="USER@HOST",
        help="Reach Ollama through an SSH tunnel to this host (or ~/.ssh/config alias).",
    )
    ssh.add_argument(
        "--ssh-port", type=int, default=SSH_PORT, help=f"SSH port (default: {SSH_PORT})."
    )
    ssh.add_argument(
        "--ssh-key", type=str, default=SSH_KEY, help="Private key file for SSH (default: ssh's own)."
    )
    ssh.add_argument(
        "--ssh-local-port",
        type=int,
        default=SSH_LOCAL_PORT,
        help=f"Local end of the tunnel (default: {SSH_LOCAL_PORT}).",
    )
    ssh.add_argument(
        "--ssh-remote-port",
        type=int,
        default=SSH_REMOTE_PORT,
        help=f"Ollama port on the server (default: {SSH_REMOTE_PORT}).",
    )

    pipeline = parser.add_argument_group("pipeline")
    pipeline.add_argument(
        "--num-tests",
        type=int,
        default=DEFAULT_NUM_TESTS,
        help=f"Number of test cases to generate (default: {DEFAULT_NUM_TESTS}).",
    )
    pipeline.add_argument(
        "--skip-validation",
        action="store_true",
        default=False,
        help="Skip sandbox compilation and validation stage.",
    )
    pipeline.add_argument(
        "--out-dir",
        type=str,
        default=str(DEFAULT_OUT_DIR),
        help=f"Output directory (default: {DEFAULT_OUT_DIR}).",
    )

    similarity = parser.add_argument_group("existing-problem check")
    similarity.add_argument(
        "--no-similarity",
        action="store_true",
        default=not SIMILARITY_ENABLED,
        help="Skip the existing-problem search in the vector database.",
    )
    similarity.add_argument(
        "--similar-k",
        type=int,
        default=SIMILARITY_TOP_K,
        help=f"Nearest problems to retrieve (default: {SIMILARITY_TOP_K}).",
    )
    similarity.add_argument(
        "--similarity-threshold",
        type=float,
        default=SIMILARITY_THRESHOLD,
        help=(
            "Cosine similarity above which the problem counts as existing "
            f"(default: {SIMILARITY_THRESHOLD})."
        ),
    )
    similarity.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Generate a package even if the problem already exists.",
    )

    polygon = parser.add_argument_group("Polygon upload")
    polygon.add_argument(
        "--no-polygon",
        action="store_true",
        default=False,
        help="Do not ask for Polygon credentials or push the package.",
    )
    polygon.add_argument(
        "--polygon-problem-id",
        type=int,
        default=None,
        help="Update this existing Polygon problem instead of creating a new one.",
    )
    polygon.add_argument(
        "--polygon-name",
        type=str,
        default=None,
        help="Name for the new Polygon problem (default: derived from the title).",
    )
    polygon.add_argument(
        "--push-unverified",
        action="store_true",
        default=False,
        help="Push to Polygon even if the package did not pass validation.",
    )
    return parser


def setup_polygon(
    args: argparse.Namespace,
    interactive: bool,
    log: Callable[[str], None] = print,
    input_fn: Callable[[str], str] = input,
    secret_fn: Optional[Callable[[str], str]] = None,
) -> Optional[PolygonTarget]:
    """
    Collect and verify Polygon credentials before the pipeline runs.

    Interactive runs always prompt (Enter keeps values from POLYGON_API_KEY /
    POLYGON_SECRET); non-interactive runs use the environment only. Returns
    None when the upload is skipped.
    """
    if args.no_polygon:
        return None

    api_key, secret = POLYGON_API_KEY, POLYGON_SECRET
    for attempt in range(1, MAX_CREDENTIAL_ATTEMPTS + 1):
        if interactive:
            kwargs = {"input_fn": input_fn}
            if secret_fn is not None:
                kwargs["secret_fn"] = secret_fn
            api_key, secret = prompt_credentials(api_key, secret, **kwargs)
        if not api_key:
            log("No Polygon API key given; the package will not be pushed to Polygon.")
            return None
        if not secret:
            log("A Polygon API secret is required with the API key.")
        else:
            client = PolygonClient(api_key=api_key, secret=secret)
            try:
                client.verify_credentials()
                log("Polygon credentials verified.")
                return PolygonTarget(client, args.polygon_problem_id, args.polygon_name)
            except PolygonAPIError as exc:
                log(f"Polygon rejected the credentials: {exc}")
        if not interactive:
            break
        api_key, secret = "", ""
        if attempt < MAX_CREDENTIAL_ATTEMPTS:
            log("Please enter them again.")

    raise AutoSetterError("could not verify the Polygon API key and secret")


def _ssh_tunnel(args: argparse.Namespace):
    if not args.ssh_host:
        return contextlib.nullcontext(None)
    return SSHTunnel(
        host=args.ssh_host,
        port=args.ssh_port,
        key_file=args.ssh_key,
        local_port=args.ssh_local_port,
        remote_port=args.ssh_remote_port,
        progress_callback=print,
    )


def _print_result(result: PipelineResult) -> None:
    print(f"\nArtifacts written to: {result.generated_dir}")
    print(f"Package assembled at: {result.package_dir}")
    if result.similar_problems:
        print("Nearest existing problem(s):")
        for match in result.similar_problems:
            print(f"  {match['score']:.4f}  {match['title']}  {match['url'] or 'N/A'}")


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main CLI entry point.

    Exit Codes:
    - 0: Package verified fit for release (and pushed to Polygon if credentials were given).
    - 1: Pipeline encountered a fatal error.
    - 2: Artifacts produced, but validation failed (package is not fit for release).
    - 3: The problem already exists in the vector database; its link was printed.
    - 4: Package verified, but the Polygon upload failed.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("AUTOSETTER_DEBUG") else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not os.path.exists(args.image_path):
        print(f"Error: Input file not found: {args.image_path}", file=sys.stderr)
        return EXIT_FAILED

    try:
        polygon = setup_polygon(args, interactive=sys.stdin.isatty())
        with _ssh_tunnel(args) as tunnel:
            result = generate_from_image(
                image_path=args.image_path,
                vision_model=args.vision_model,
                text_model=args.text_model,
                ollama_host=tunnel.local_url if tunnel else args.host,
                num_tests=args.num_tests,
                skip_validation=args.skip_validation,
                out_dir=args.out_dir,
                similarity_check=not args.no_similarity,
                similar_k=args.similar_k,
                similarity_threshold=args.similarity_threshold,
                force=args.force,
            )
    except (AutoSetterError, SSHTunnelError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_FAILED
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    existing = result.existing_problem
    if existing is not None:
        print(
            f"\nThis problem already exists ({existing['score']:.2%} similar): "
            f"{existing['title']}"
        )
        print(existing["url"] or "No link is stored for it in the vector database.")
        print("Re-run with --force to generate a package anyway.")
        return EXIT_EXISTING_PROBLEM

    _print_result(result)

    if result.ready_for_release:
        print(f"Ready for release — {result.summary}")
    else:
        print(f"NOT ready for release — {result.summary}", file=sys.stderr)
        if result.report is not None and not result.report.validator_trusted:
            print(
                "  The validator disagrees with the problem's own samples; fix validator "
                "or constraints before release.",
                file=sys.stderr,
            )

    if polygon is None:
        return EXIT_RELEASED if result.ready_for_release else EXIT_NOT_RELEASABLE

    if not result.ready_for_release and not args.push_unverified:
        print(
            "Not pushing an unverified package to Polygon. Push it anyway with:\n"
            f"  python -m autosetter.polygon {result.package_dir}",
            file=sys.stderr,
        )
        return EXIT_NOT_RELEASABLE

    print("\nPushing package to Polygon...")
    try:
        published = publish_package(
            result.package_dir,
            polygon.client,
            problem_id=polygon.problem_id,
            name=polygon.name,
        )
    except PolygonAPIError as exc:
        print(f"Polygon upload failed: {exc}", file=sys.stderr)
        print(
            f"Retry with: python -m autosetter.polygon {result.package_dir}",
            file=sys.stderr,
        )
        return EXIT_POLYGON_FAILED

    print(f"Polygon problem: {published.name} (ID {published.problem_id})")
    return EXIT_RELEASED if result.ready_for_release else EXIT_NOT_RELEASABLE


if __name__ == "__main__":
    sys.exit(main())
