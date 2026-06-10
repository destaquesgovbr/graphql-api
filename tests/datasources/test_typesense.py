from datetime import datetime, timezone
from unittest.mock import MagicMock

from graphql_api.datasources.typesense import TypesenseDatasource


def _make_hit(doc: dict) -> dict:
    return {"document": doc}


def _sample_doc(**overrides) -> dict:
    base = {
        "unique_id": "abc-123",
        "title": "Test Article",
        "url": "https://example.com/article",
        "image": "https://example.com/img.jpg",
        "video_url": None,
        "content": "Full article content",
        "summary": "Short summary",
        "subtitle": "A subtitle",
        "editorial_lead": "Lead text",
        "category": "politica",
        "tags": ["economia", "saude"],
        "agency": "agencia-brasil",
        "agency_name": "Agência Brasil",
        "published_at": 1700000000,
        "extracted_at": 1700001000,
    }
    base.update(overrides)
    return base


def _mock_client(search_return=None, retrieve_return=None, retrieve_side_effect=None):
    client = MagicMock()
    collection = MagicMock()
    client.collections.__getitem__ = MagicMock(return_value=collection)

    if search_return is not None:
        collection.documents.search.return_value = search_return

    if retrieve_return is not None:
        doc_mock = MagicMock()
        doc_mock.retrieve.return_value = retrieve_return
        collection.documents.__getitem__ = MagicMock(return_value=doc_mock)
    elif retrieve_side_effect is not None:
        doc_mock = MagicMock()
        doc_mock.retrieve.side_effect = retrieve_side_effect
        collection.documents.__getitem__ = MagicMock(return_value=doc_mock)

    return client


class TestSearchArticles:
    def test_search_articles_returns_typed_result(self):
        doc = _sample_doc()
        client = _mock_client(search_return={"hits": [_make_hit(doc)], "found": 1})
        ds = TypesenseDatasource(client)

        result = ds.search_articles(page=1, limit=10)

        assert result.found == 1
        assert result.page == 1
        assert len(result.articles) == 1
        article = result.articles[0]
        assert article.unique_id == "abc-123"
        assert article.title == "Test Article"
        assert article.url == "https://example.com/article"
        assert article.agency == "agencia-brasil"
        assert article.agency_name == "Agência Brasil"
        assert article.tags == ["economia", "saude"]
        assert article.published_at == datetime.fromtimestamp(1700000000, tz=timezone.utc)
        assert article.extracted_at == datetime.fromtimestamp(1700001000, tz=timezone.utc)

    def test_search_articles_filters_by_agency(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(agencies=["agencia-brasil"])

        call_args = client.collections["news"].documents.search.call_args[0][0]
        assert "agency:" in call_args["filter_by"]
        assert "agencia-brasil" in call_args["filter_by"]

    def test_search_articles_filters_by_date_range(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(start_date="2024-01-01T00:00:00+00:00", end_date="2024-12-31T23:59:59+00:00")

        call_args = client.collections["news"].documents.search.call_args[0][0]
        assert "published_at:>=" in call_args["filter_by"]
        assert "published_at:<=" in call_args["filter_by"]

    def test_search_articles_filters_by_themes(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(themes=["01", "02"])

        call_args = client.collections["news"].documents.search.call_args[0][0]
        assert "theme_1_level_1_code:=01" in call_args["filter_by"]
        assert "theme_1_level_1_code:=02" in call_args["filter_by"]


class TestThemeFieldsMapping:
    def test_maps_eight_theme_fields_from_document(self):
        doc = _sample_doc(
            theme_1_level_1_code="01",
            theme_1_level_1_label="Economia",
            theme_1_level_2_code="0102",
            theme_1_level_2_label="Trabalho",
            theme_1_level_3_code="010203",
            theme_1_level_3_label="Emprego",
            most_specific_theme_code="010203",
            most_specific_theme_label="Emprego",
        )
        client = _mock_client(search_return={"hits": [_make_hit(doc)], "found": 1})
        ds = TypesenseDatasource(client)

        article = ds.search_articles().articles[0]

        assert article.theme_1_level_1_code == "01"
        assert article.theme_1_level_1_label == "Economia"
        assert article.theme_1_level_2_code == "0102"
        assert article.theme_1_level_2_label == "Trabalho"
        assert article.theme_1_level_3_code == "010203"
        assert article.theme_1_level_3_label == "Emprego"
        assert article.most_specific_theme_code == "010203"
        assert article.most_specific_theme_label == "Emprego"

    def test_theme_fields_default_to_none_when_absent(self):
        doc = _sample_doc()  # sem campos de tema
        client = _mock_client(search_return={"hits": [_make_hit(doc)], "found": 1})
        ds = TypesenseDatasource(client)

        article = ds.search_articles().articles[0]

        assert article.theme_1_level_1_code is None
        assert article.most_specific_theme_label is None


class TestThemesOrAcrossLevels:
    def test_themes_or_across_three_levels(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(themes=["01", "02"])

        filter_by = client.collections["news"].documents.search.call_args[0][0]["filter_by"]
        # OR através de L1/L2/L3 code para cada theme informado
        assert "theme_1_level_1_code:=01" in filter_by
        assert "theme_1_level_2_code:=01" in filter_by
        assert "theme_1_level_3_code:=01" in filter_by
        assert "theme_1_level_1_code:=02" in filter_by
        assert "theme_1_level_2_code:=02" in filter_by
        assert "theme_1_level_3_code:=02" in filter_by
        assert "||" in filter_by

    def test_theme_label_filters_level_1_label(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(theme_label="Economia")

        filter_by = client.collections["news"].documents.search.call_args[0][0]["filter_by"]
        assert "theme_1_level_1_label:=Economia" in filter_by

    def test_tags_filter(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(tags=["economia", "saude"])

        filter_by = client.collections["news"].documents.search.call_args[0][0]["filter_by"]
        assert "tags:[" in filter_by
        assert "economia" in filter_by
        assert "saude" in filter_by


class TestDedup:
    def test_dedup_true_injects_group_by_content_hash(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(dedup=True)

        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["group_by"] == "content_hash"
        assert params["group_limit"] == 1

    def test_dedup_false_omits_group_by(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(dedup=False)

        params = client.collections["news"].documents.search.call_args[0][0]
        assert "group_by" not in params
        assert "group_limit" not in params

    def test_dedup_grouped_hits_are_flattened(self):
        doc = _sample_doc()
        grouped = {
            "grouped_hits": [{"group_key": ["h1"], "hits": [_make_hit(doc)]}],
            "found": 1,
        }
        client = _mock_client(search_return=grouped)
        ds = TypesenseDatasource(client)

        result = ds.search_articles(dedup=True)

        assert len(result.articles) == 1
        assert result.articles[0].unique_id == "abc-123"


class TestAlphaPassthrough:
    def test_alpha_passed_into_vector_query(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(vector=[0.1, 0.2, 0.3], alpha=0.8)

        params = client.collections["news"].documents.search.call_args[0][0]
        assert "alpha:0.8" in params["vector_query"]
        assert "content_embedding:" in params["vector_query"]

    def test_alpha_defaults_to_0_3_when_none(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(vector=[0.1, 0.2, 0.3], alpha=None)

        params = client.collections["news"].documents.search.call_args[0][0]
        assert "alpha:0.3" in params["vector_query"]


class TestThemeCounts:
    def test_theme_counts_builds_group_by_and_filter(self):
        grouped = {
            "grouped_hits": [
                {"group_key": ["01"], "found": 42},
                {"group_key": ["02"], "found": 17},
            ],
            "found": 59,
        }
        client = _mock_client(search_return=grouped)
        ds = TypesenseDatasource(client)

        result = ds.theme_counts(level=1, days=30)

        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["group_by"] == "theme_1_level_1_code"
        assert params["per_page"] == 250
        assert "published_at:>=" in params["filter_by"]
        assert result == [("01", 42), ("02", 17)]

    def test_theme_counts_respects_level(self):
        client = _mock_client(search_return={"grouped_hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.theme_counts(level=2, days=7)

        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["group_by"] == "theme_1_level_2_code"


class TestKeywordQuery:
    def test_keyword_sets_q_and_query_by(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(q="reforma tributaria", query_by="title,summary")

        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["q"] == "reforma tributaria"
        assert params["query_by"] == "title,summary"

    def test_default_q_star_omits_query_by(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles()

        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["q"] == "*"
        assert "query_by" not in params

    def test_q_star_with_query_by_still_omits_query_by(self):
        # q="*" (wildcard default) preserva comportamento legado mesmo com
        # query_by fornecido — não há busca de keyword real.
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(q="*", query_by="title,summary")

        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["q"] == "*"
        assert "query_by" not in params


class TestSortByOverride:
    def test_default_sort_by_published_at_desc(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles()

        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["sort_by"] == "published_at:desc"

    def test_sort_by_none_omits_key(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(sort_by=None)

        params = client.collections["news"].documents.search.call_args[0][0]
        assert "sort_by" not in params

    def test_sort_by_custom_value(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(sort_by="title:asc")

        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["sort_by"] == "title:asc"


class TestBackwardCompat:
    def test_existing_call_without_new_params_works(self):
        doc = _sample_doc()
        client = _mock_client(search_return={"hits": [_make_hit(doc)], "found": 1})
        ds = TypesenseDatasource(client)

        result = ds.search_articles(
            page=1,
            limit=10,
            agencies=["agencia-brasil"],
            themes=["01"],
            start_date="2024-01-01T00:00:00+00:00",
            end_date="2024-12-31T23:59:59+00:00",
            tags=["economia"],
        )

        params = client.collections["news"].documents.search.call_args[0][0]
        assert "group_by" not in params
        assert "vector_query" not in params
        assert result.found == 1


class TestGetArticleById:
    def test_get_article_by_id_uses_search_with_filter_by(self):
        doc = _sample_doc()
        client = _mock_client(search_return={"hits": [_make_hit(doc)], "found": 1})
        ds = TypesenseDatasource(client)

        article = ds.get_article_by_id("abc-123")

        assert article is not None
        assert article.unique_id == "abc-123"
        assert article.title == "Test Article"

        # Deve usar busca (permitida pela search-only key), não .documents[id].retrieve()
        params = client.collections["news"].documents.search.call_args[0][0]
        assert "filter_by" in params
        assert "unique_id:=" in params["filter_by"]
        assert "abc-123" in params["filter_by"]
        assert params["per_page"] == 1

    def test_get_article_by_id_does_not_call_retrieve(self):
        doc = _sample_doc()
        client = _mock_client(search_return={"hits": [_make_hit(doc)], "found": 1})
        ds = TypesenseDatasource(client)

        ds.get_article_by_id("abc-123")

        # Nenhum acesso a documents[id] (operação não permitida pela search-only key)
        collection = client.collections["news"]
        assert not collection.documents.__getitem__.called

    def test_get_article_not_found_returns_none(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        article = ds.get_article_by_id("nonexistent")

        assert article is None


class TestGetArticlesByIds:
    def test_returns_dict_keyed_by_unique_id(self):
        d1 = _sample_doc(unique_id="a", title="A")
        d2 = _sample_doc(unique_id="b", title="B")
        client = _mock_client(
            search_return={"hits": [_make_hit(d1), _make_hit(d2)], "found": 2}
        )
        ds = TypesenseDatasource(client)

        result = ds.get_articles_by_ids(["a", "b"])

        assert set(result.keys()) == {"a", "b"}
        assert result["a"].title == "A"
        assert result["b"].title == "B"
        # Usa busca com filtro IN (compatível com a search-only key).
        params = client.collections["news"].documents.search.call_args[0][0]
        assert "unique_id:[" in params["filter_by"]
        assert "`a`" in params["filter_by"]
        assert "`b`" in params["filter_by"]

    def test_empty_ids_returns_empty_no_search(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        result = ds.get_articles_by_ids([])

        assert result == {}
        assert not client.collections["news"].documents.search.called

    def test_missing_ids_simply_absent_from_dict(self):
        d1 = _sample_doc(unique_id="a", title="A")
        client = _mock_client(search_return={"hits": [_make_hit(d1)], "found": 1})
        ds = TypesenseDatasource(client)

        result = ds.get_articles_by_ids(["a", "ghost"])

        assert "a" in result
        assert "ghost" not in result


class TestEntitySentimentFilters:
    def test_filters_by_entities(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(entities=["Ministério da Saúde", "Brasília"])

        fb = client.collections["news"].documents.search.call_args[0][0]["filter_by"]
        assert "entities:[" in fb
        assert "Ministério da Saúde" in fb
        assert "Brasília" in fb

    def test_filters_by_sentiment(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(sentiment=["positive", "neutral"])

        fb = client.collections["news"].documents.search.call_args[0][0]["filter_by"]
        assert "sentiment_label:[" in fb
        assert "positive" in fb
        assert "neutral" in fb

    def test_entity_values_are_backtick_quoted(self):
        # Crases são load-bearing: sem elas valores multi-palavra quebram a
        # sintaxe do filter_by do Typesense.
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(entities=["Ministério da Saúde"])

        fb = client.collections["news"].documents.search.call_args[0][0]["filter_by"]
        assert "entities:[`Ministério da Saúde`]" in fb

    def test_entities_sentiment_combine_with_and(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(
            agencies=["mec"], entities=["Brasília"], sentiment=["positive"]
        )

        fb = client.collections["news"].documents.search.call_args[0][0]["filter_by"]
        assert " && " in fb
        assert "entities:[`Brasília`]" in fb
        assert "sentiment_label:[`positive`]" in fb

    def test_filters_by_entity_canonical(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(entity_canonical=["Q216330", "dgb_abc"])

        fb = client.collections["news"].documents.search.call_args[0][0]["filter_by"]
        assert "entity_canonical:[" in fb
        assert "`Q216330`" in fb
        assert "`dgb_abc`" in fb

    def test_entity_canonical_combines_with_entities(self):
        client = _mock_client(search_return={"hits": [], "found": 0})
        ds = TypesenseDatasource(client)

        ds.search_articles(entities=["Brasília"], entity_canonical=["Q216330"])

        fb = client.collections["news"].documents.search.call_args[0][0]["filter_by"]
        assert " && " in fb
        assert "entities:[`Brasília`]" in fb
        assert "entity_canonical:[`Q216330`]" in fb


class TestEntityFacets:
    def test_default_field_and_facet_query(self):
        client = _mock_client(
            search_return={
                "facet_counts": [
                    {
                        "field_name": "entities",
                        "counts": [
                            {"value": "Ministério da Saúde", "count": 12},
                            {"value": "Brasília", "count": 5},
                        ],
                    }
                ]
            }
        )
        ds = TypesenseDatasource(client)

        result = ds.entity_facets(query="min", limit=10)

        assert result == [("Ministério da Saúde", 12), ("Brasília", 5)]
        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["facet_by"] == "entities"
        assert params["facet_query"] == "entities:min"
        assert params["max_facet_values"] == 10
        assert params["per_page"] == 0

    def test_typed_field_org(self):
        client = _mock_client(search_return={"facet_counts": []})
        ds = TypesenseDatasource(client)

        ds.entity_facets(query="", entity_type="ORG", limit=5)

        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["facet_by"] == "entity_org"
        # query vazia → sem facet_query (lista os top facets)
        assert "facet_query" not in params

    def test_unknown_type_falls_back_to_combined(self):
        client = _mock_client(search_return={"facet_counts": []})
        ds = TypesenseDatasource(client)

        ds.entity_facets(query="x", entity_type="WHATEVER", limit=5)

        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["facet_by"] == "entities"

    def test_typed_fields_per_and_loc(self):
        for entity_type, field in (("PER", "entity_per"), ("LOC", "entity_loc")):
            client = _mock_client(search_return={"facet_counts": []})
            ds = TypesenseDatasource(client)
            ds.entity_facets(query="", entity_type=entity_type, limit=5)
            params = client.collections["news"].documents.search.call_args[0][0]
            assert params["facet_by"] == field

    def test_typed_fields_event_policy_canonical(self):
        # Fase 4: EVENT/POLICY (eram descartados) + CANONICAL (entity_canonical).
        for entity_type, field in (
            ("EVENT", "entity_event"),
            ("POLICY", "entity_policy"),
            ("CANONICAL", "entity_canonical"),
        ):
            client = _mock_client(search_return={"facet_counts": []})
            ds = TypesenseDatasource(client)
            ds.entity_facets(query="", entity_type=entity_type, limit=5)
            params = client.collections["news"].documents.search.call_args[0][0]
            assert params["facet_by"] == field

    def test_entity_type_is_uppercased(self):
        # Cliente pode mandar "org" minúsculo; o datasource normaliza com .upper().
        client = _mock_client(search_return={"facet_counts": []})
        ds = TypesenseDatasource(client)
        ds.entity_facets(query="", entity_type="org", limit=5)
        params = client.collections["news"].documents.search.call_args[0][0]
        assert params["facet_by"] == "entity_org"
