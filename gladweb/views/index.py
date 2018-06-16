from itertools import groupby

import os
import tempfile
import zipfile
from collections import namedtuple
from flask import Blueprint, request, render_template, g, url_for, redirect, flash, current_app
from urllib import urlencode

from glad.parse import FeatureSet
from glad.util import parse_version
from gladweb.exception import GladWebException, WebValueError
from gladweb.util import write_dir_to_zipfile


Version = namedtuple('Version', ['major', 'minor'])


index = Blueprint('index', __name__)


@index.route('/', methods=['GET'])
def landing():
    return render_template(
        'index.html', **g.metadata.as_dict()
    )


def glad_generate():
    # Form data
    apis = dict(api.split('=') for api in request.form.getlist('api'))
    profiles = dict(p.split('=') for p in request.form.getlist('profile'))
    generator = request.form.get('generator')
    extensions = request.form.getlist('extensions')
    options = set(request.form.getlist('options'))

    # Other
    # the suffix is required because mkdtemp sometimes creates directories with an
    # underscore at the end, we later use werkzeug.utils.secure_filename on that directory,
    # this function happens to strip underscores...
    out_path = tempfile.mkdtemp(dir=current_app.config['TEMP'], suffix='glad')
    os.chmod(out_path, 0o750)

    merge = 'MERGE' in options
    if merge:
        options.remove('MERGE')

    Generator = g.metadata.get_generator_for_name(generator)
    config = Generator.Config()
    # TODO: more than just boolean configs
    for option in options:
        config.set(option, True)
    config.validate()
    generator = Generator(out_path, opener=g.opener)

    apis_by_spec = list(groupby(
        [(api, version) for api, version in apis.items() if not version.lower().strip() == 'none'],
        key=lambda api_version: g.metadata.get_specification_name_for_api(api_version[0])
    ))

    if len(apis_by_spec) == 0:
        raise WebValueError('no API selected')

    def select(specification, api, version):
        profile = profiles.get(api)
        filtered_extensions = [ext for ext in extensions if specification.is_extension(api, ext)]
        return generator.select(specification, api, version, profile, filtered_extensions, config)

    for spec_name, apis in apis_by_spec:
        specification = g.metadata.get_specification(spec_name)
        feature_sets = list(select(specification, api, parse_version(version)) for api, version in apis)

        if merge and len(feature_sets) > 1:
            feature_sets = [FeatureSet.merge(*feature_sets)]

        for feature_set in feature_sets:
            generator.generate(specification, feature_set, config)

    with zipfile.ZipFile(os.path.join(out_path, 'glad.zip'), mode='w') as zipf:
        write_dir_to_zipfile(out_path, zipf, exclude=['glad.zip'])

    # TODO replace 'none' check with proper comparison for default value
    params = [(name, ','.join(option for option in options if not 'none' in option))
              for name, options in request.form.lists()]
    serialized = urlencode(params)

    # Poor mans database
    serialized_path = os.path.join(out_path, '.serialized')
    with open(serialized_path, 'w') as fobj:
        fobj.write(serialized)

    name = os.path.split(out_path)[1]
    if current_app.config['FREEZE']:
        current_app.freezer.freeze(name)
    return url_for('generated.autoindex', root=name)


@index.route('/generate', methods=['POST'])
def generate():
    try:
        url = glad_generate()
    except GladWebException, e:
        current_app.logger.info('user error: %s', e)
        flash(e.message, category='error')
        return redirect(url_for('index.landing'))
    except Exception, e:
        current_app.logger.exception(e)
        current_app.logger.error(request.form)
        flash(e.message, category='error')
        return redirect(url_for('index.landing'))

    return redirect(url)
