"""Public website (SRS 5.14) and enquiry form (FR-16.1). No login needed."""
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import PlainTextResponse, Response

from common.db import insert
from common.web import BUSINESS, render

router = APIRouter(tags=["website"])

PAGES = {
    "/": "website/home.html",
    "/services": "website/services.html",
    "/umrah": "website/umrah.html",
    "/about": "website/about.html",
    "/contact": "website/contact.html",
    "/privacy": "website/privacy.html",
    "/terms": "website/terms.html",
}


def _page(template):
    def view(request: Request):
        return render(request, template)
    return view


for path, template in PAGES.items():
    router.add_api_route(path, _page(template), methods=["GET"], include_in_schema=False)


def _base_url(request: Request):
    return BUSINESS["site_url"] or str(request.base_url).rstrip("/")


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    base = _base_url(request)
    urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in PAGES)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(xml, media_type="application/xml")


@router.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    return PlainTextResponse(
        f"User-agent: *\nDisallow: /app\nDisallow: /my\nDisallow: /r/\nDisallow: /login\n"
        f"Sitemap: {_base_url(request)}/sitemap.xml\n"
    )


@router.post("/enquiry")
def enquiry(
    request: Request,
    name: str = Form("", max_length=120),
    mobile: str = Form("", max_length=20),
    service: str = Form("", max_length=60),
    message: str = Form("", max_length=1000),
):
    form = {"name": name.strip(), "mobile": re.sub(r"\D", "", mobile), "service": service, "message": message.strip()}
    if len(form["mobile"]) == 12 and form["mobile"].startswith("91"):
        form["mobile"] = form["mobile"][2:]
    errors = {}
    if not form["name"]:
        errors["name"] = "Apna naam likhiye."
    if not re.fullmatch(r"[6-9]\d{9}", form["mobile"]):
        errors["mobile"] = "Sahi 10 digit ka mobile number likhiye."
    if errors:
        return render(request, "website/_enquiry_form.html", {"form": form, "errors": errors})

    insert("enquiries", {
        "name": form["name"], "mobile": form["mobile"],
        "service": service or None, "message": form["message"] or None,
    })
    return render(request, "website/_enquiry_thanks.html", form)
