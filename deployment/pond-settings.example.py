"""Copy OUTSIDE the repository/quarantine; load with POND_SETTINGS=/absolute/path.

This file is trusted Python code executed by Flask. Restrict write permissions
and use the same file for web and workers. Never accept it from an uploader.
"""
UPLOAD_QUARANTINE_ROOT = '/srv/pond-private/quarantine'
SQLALCHEMY_BINDS = {'pond': 'sqlite:////srv/pond-private/the_pond.db'}

# Replace None with trusted, site-specific implementations after review.
# from your_site.ingestion import verify_template, inspect_image, publication_adapter
# INGESTION_VERIFY_TEMPLATE = verify_template
# INGESTION_INSPECT_IMAGE = inspect_image
# INGESTION_PUBLICATION_ADAPTER = publication_adapter
INGESTION_VERIFY_TEMPLATE = None
INGESTION_INSPECT_IMAGE = None
INGESTION_PUBLICATION_ADAPTER = None
