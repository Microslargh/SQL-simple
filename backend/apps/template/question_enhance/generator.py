from apps.template.template import get_base_template


def get_question_enhance_template():
    template = get_base_template()
    return template['template']['question_enhance']
