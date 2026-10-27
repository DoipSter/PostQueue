"""What makes PostQueue a finished product rather than a developer tool: one Meta app and one hosting service, shipped
inside the program, so a user only presses Sign in.

Fill these in once (see BUILD_AND_TEST_GUIDE.md, section "Publishing setup"). While they are empty, setup falls back to
the advanced path: the user creates their own Meta app and Cloudinary account.

Environment variables POSTQUEUE_META_APP_ID and POSTQUEUE_SERVICE_URL override them, which the tests use."""
import os

# The App ID of the Meta app registered for PostQueue (App settings > Basic). The app secret never ships: the service
# below holds it and does the token exchange.
META_APP_ID = os.environ.get('POSTQUEUE_META_APP_ID', '')

# The PostQueue service (service/ in the repository, deployed as a Cloudflare Worker): token exchange and temporary
# media hosting. For example https://postqueue.example.workers.dev
SERVICE_URL = os.environ.get('POSTQUEUE_SERVICE_URL', '').rstrip('/')

# Which Meta sign-in the built-in app uses by default: 'facebook' (Instagram account linked to a Facebook Page) or
# 'instagram' (Business Login for Instagram). Both products must be added to the Meta app for the choice to matter.
DEFAULT_LOGIN_KIND = 'facebook'


def simple_setup():
    """True when the built-in app and service are configured, so setup needs no developer steps."""
    return bool(META_APP_ID and SERVICE_URL)
