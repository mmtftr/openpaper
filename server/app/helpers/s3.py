import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from app.database.crud.paper_crud import PaperUpdate, paper_crud
from app.database.models import Paper
from app.schemas.user import CurrentUser

logger = logging.getLogger(__name__)

# Load AWS configuration from environment variables
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
CLOUDFLARE_BUCKET_NAME = os.environ.get("CLOUDFLARE_BUCKET_NAME")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
S3_PUBLIC_BASE_URL = os.environ.get("S3_PUBLIC_BASE_URL")


class S3Service:
    """Service for handling S3 operations"""

    def __init__(self):
        """Initialize S3 client"""
        self.s3_client = boto3.client(
            "s3",  # type: ignore
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
            endpoint_url=S3_ENDPOINT_URL,
            config=Config(s3={"addressing_style": "path"}),
        )
        self.bucket_name = S3_BUCKET_NAME
        self.cloudflare_bucket_name = CLOUDFLARE_BUCKET_NAME

    def _public_url(self, object_key: str) -> str:
        if S3_PUBLIC_BASE_URL:
            return f"{S3_PUBLIC_BASE_URL.rstrip('/')}/{object_key}"
        return f"https://{self.cloudflare_bucket_name}/{object_key}"

    def generate_presigned_url(
        self, object_key: str, expiration: int = 86400
    ) -> Optional[str]:
        """
        Generate a presigned URL for a file

        Args:
            object_key: The S3 object key
            expiration: URL expiration time in seconds (default: 24 hours)

        Returns:
            str: Presigned URL or None if error
        """
        try:
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": object_key},
                ExpiresIn=expiration,
            )

            if S3_ENDPOINT_URL and S3_PUBLIC_BASE_URL:
                internal_base = f"{S3_ENDPOINT_URL.rstrip('/')}/{self.bucket_name}"
                url = url.replace(internal_base, S3_PUBLIC_BASE_URL.rstrip("/"), 1)

            # Replace the S3 URL with Cloudflare URL
            if url.startswith(f"https://{self.bucket_name}.s3.amazonaws.com/"):
                url = url.replace(
                    f"https://{self.bucket_name}.s3.amazonaws.com/",
                    f"https://{self.cloudflare_bucket_name}/",
                )
            elif url.startswith(
                f"https://{self.bucket_name}.s3.us-east-1.amazonaws.com/"
            ):
                url = url.replace(
                    f"https://{self.bucket_name}.s3.us-east-1.amazonaws.com/",
                    f"https://{self.cloudflare_bucket_name}/",
                )
            elif url.startswith(
                f"https://{self.bucket_name}.s3.{AWS_REGION}.amazonaws.com/"
            ):
                url = url.replace(
                    f"https://{self.bucket_name}.s3.{AWS_REGION}.amazonaws.com/",
                    f"https://{self.cloudflare_bucket_name}/",
                )

            return url
        except ClientError as e:
            logger.error(f"Error generating presigned URL: {e}")
            return None

    def get_object_bytes(self, object_key: str) -> bytes:
        """
        Fetch the raw bytes of an S3 object directly via boto3.

        Used when the server itself needs the file (e.g. for sending to an
        LLM). Bypasses the public presigned URL — the container can talk to
        MinIO/S3 directly and shouldn't be making egress through the public
        host to fetch its own objects.
        """
        response = self.s3_client.get_object(Bucket=self.bucket_name, Key=object_key)
        return response["Body"].read()

    def get_file_size_in_kb(self, object_key: str) -> Optional[int]:
        """
        Get the size of a file in KB from S3

        Args:
            object_key: The S3 object key
            db: Database session
            paper_id: The paper ID to cache the size for
            current_user: Current user for ownership verification

        Returns:
            int: Size in KB or None if error
        """
        try:
            response = self.s3_client.head_object(
                Bucket=self.bucket_name, Key=object_key
            )
            size_in_kb = response.get("ContentLength", 0) // 1024
            return size_in_kb
        except ClientError as e:
            logger.error(f"Error getting file size for {object_key}: {e}")
            return None

    def get_cached_presigned_url(
        self,
        db: Session,
        paper_id: str,
        object_key: str,
        expiration: int = 86400,
        current_user: Optional[CurrentUser] = None,
    ) -> Optional[str]:
        """
        Get a cached presigned URL or generate a new one if expired/missing

        Args:
            db: Database session
            paper_id: The paper ID to cache the URL for
            object_key: The S3 object key
            expiration: URL expiration time in seconds (default: 24 hours)
            current_user: Current user for ownership verification

        Returns:
            str: Presigned URL or None if error
        """
        try:
            # Get the paper using CRUD
            paper = paper_crud.get(db, id=paper_id, user=current_user)
            if not paper:
                return None

            # Check if we have a valid cached URL
            now = datetime.now(timezone.utc)
            if (
                paper.cached_presigned_url
                and paper.presigned_url_expires_at
                and paper.presigned_url_expires_at > now
            ):
                logger.debug(f"Using cached presigned URL for paper {paper_id}")
                return str(paper.cached_presigned_url)

            # Generate new presigned URL
            url = self.generate_presigned_url(object_key, expiration)
            if not url:
                return None

            # Cache the URL with expiration (subtract 5 minutes for safety buffer)
            expires_at = now + timedelta(seconds=expiration - 300)

            # Calculate the size of the file in KB
            current_size = getattr(paper, "size_in_kb", None)
            if current_size is not None:
                size_in_kb: Optional[int] = current_size
            else:
                size_in_kb = self.get_file_size_in_kb(object_key)

            # Update using CRUD
            updated_paper = paper_crud.update(
                db=db,
                db_obj=paper,
                obj_in=PaperUpdate(
                    cached_presigned_url=url,
                    presigned_url_expires_at=expires_at,
                    size_in_kb=size_in_kb,
                ),
                user=current_user,
            )

            if not updated_paper:
                logger.error(f"Failed to update cached URL for paper {paper_id}")
                return None

            logger.debug(f"Generated and cached new presigned URL for paper {paper_id}")
            return url

        except Exception as e:
            logger.error(f"Error getting cached presigned URL: {e}")
            return None

    def invalidate_cached_url(
        self, db: Session, paper_id: str, current_user: Optional[CurrentUser] = None
    ) -> bool:
        """
        Invalidate the cached presigned URL for a paper

        Args:
            db: Database session
            paper_id: The paper ID to invalidate
            current_user: Current user for ownership verification

        Returns:
            bool: True if invalidated successfully
        """

        try:
            paper = paper_crud.get(db, id=paper_id, user=current_user)
            if not paper:
                return False

            # Update using CRUD to clear cached URL
            updated_paper = paper_crud.update(
                db=db,
                db_obj=paper,
                obj_in=PaperUpdate(
                    cached_presigned_url=None, presigned_url_expires_at=None
                ),
                user=current_user,
            )

            return updated_paper is not None

        except Exception as e:
            logger.error(f"Error invalidating cached URL: {e}")
            return False

    def get_cached_presigned_urls_bulk(
        self,
        db: Session,
        papers: List[Paper],
        expiration: int = 86400,
    ) -> Dict[str, Optional[str]]:
        """
        Bulk retrieve presigned URLs for multiple papers, parallelizing S3 calls for expired URLs.

        This method optimizes for the common case where most URLs are cached:
        1. First pass: identify which papers have valid cached URLs (fast, sequential DB reads)
        2. Second pass: generate new URLs for expired/missing ones (parallelized S3 API calls)
        3. Third pass: update the database with new URLs (sequential DB writes)

        Args:
            db: Database session
            papers: List of Paper objects to get URLs for
            expiration: URL expiration time in seconds (default: 24 hours)

        Returns:
            Dict mapping paper_id (str) to presigned URL (or None if error)
        """
        from app.database.crud.paper_crud import PaperUpdate, paper_crud

        result: Dict[str, Optional[str]] = {}
        papers_needing_urls: List[Paper] = []
        now = datetime.now(timezone.utc)

        # First pass: check cache status for all papers (fast, sequential)
        for paper in papers:
            paper_id = str(paper.id)

            # Check if we have a valid cached URL
            if (
                paper.cached_presigned_url
                and paper.presigned_url_expires_at
                and paper.presigned_url_expires_at > now
            ):
                result[paper_id] = str(paper.cached_presigned_url)
                logger.debug(f"Using cached presigned URL for paper {paper_id}")
            else:
                papers_needing_urls.append(paper)

        if not papers_needing_urls:
            return result

        logger.info(
            f"Generating {len(papers_needing_urls)} new presigned URLs in parallel"
        )

        # Second pass: generate new URLs in parallel (no DB access, just S3 API calls)
        def generate_url_for_paper(
            paper: Paper,
        ) -> tuple[str, Optional[str], Optional[int]]:
            """Generate URL and file size for a single paper"""
            try:
                url = self.generate_presigned_url(str(paper.s3_object_key), expiration)

                # Get file size if not already cached
                size_in_kb = None
                if paper.size_in_kb is None and url:
                    size_in_kb = self.get_file_size_in_kb(str(paper.s3_object_key))

                return (str(paper.id), url, size_in_kb)
            except Exception as e:
                logger.error(f"Error generating URL for paper {paper.id}: {e}")
                return (str(paper.id), None, None)

        # Use ThreadPoolExecutor for parallel S3 API calls
        new_urls: Dict[str, Optional[str]] = {}
        papers_to_update: Dict[str, tuple[Paper, str, Optional[int]]] = {}

        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_paper = {
                executor.submit(generate_url_for_paper, paper): paper
                for paper in papers_needing_urls
            }

            for future in as_completed(future_to_paper):
                paper_id, url, size_in_kb = future.result()
                new_urls[paper_id] = url

                if url:
                    paper = future_to_paper[future]
                    papers_to_update[paper_id] = (paper, url, size_in_kb)

        # Third pass: update database with new URLs (sequential DB writes)
        expires_at = now + timedelta(seconds=expiration - 300)

        for paper_id, (paper, url, size_in_kb) in papers_to_update.items():
            try:
                update_data = PaperUpdate(
                    cached_presigned_url=url,
                    presigned_url_expires_at=expires_at,
                )

                # Only update size if we got a new value
                if size_in_kb is not None:
                    update_data.size_in_kb = size_in_kb

                paper_crud.update(
                    db=db,
                    db_obj=paper,
                    obj_in=update_data,
                    user=None,  # Bulk operation, skip user check
                )
                result[paper_id] = url
                logger.debug(f"Cached new presigned URL for paper {paper_id}")
            except Exception as e:
                logger.error(f"Error updating cached URL for paper {paper_id}: {e}")
                result[paper_id] = url  # Still return the URL even if caching failed

        # Add any papers that failed to generate URLs
        for paper_id, url in new_urls.items():
            if paper_id not in result:
                result[paper_id] = url

        return result


# Create a single instance to use throughout the application
s3_service = S3Service()
