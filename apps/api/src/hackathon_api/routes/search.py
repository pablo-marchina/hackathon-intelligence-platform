from fastapi import APIRouter
from hackathon_api.schemas import NaturalLanguageParseRequest, NaturalLanguageParseResponse
from hackathon_api.search_parser import parse_natural_language_query

router = APIRouter(prefix="/search", tags=["search"])


@router.post("/parse", response_model=NaturalLanguageParseResponse)
def parse_query(payload: NaturalLanguageParseRequest):
    return parse_natural_language_query(payload.query)
