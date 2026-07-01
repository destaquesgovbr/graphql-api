from graphql_api.schema.types.theme import Agency


def _agency(code):
    return Agency(code=code, label=code)


def test_agencia_brasil_e_republisher():
    assert _agency("agencia_brasil").is_republisher() is True


def test_ministerio_saude_nao_e_republisher():
    assert _agency("ministerio_da_saude").is_republisher() is False


def test_ebc_e_republisher():
    assert _agency("ebc").is_republisher() is True


def test_tvbrasil_e_republisher():
    assert _agency("tvbrasil").is_republisher() is True


def test_is_republisher_exposto_no_schema():
    from graphql_api.schema import schema

    assert "isRepublisher" in schema.as_str()
