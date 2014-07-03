import superdesk
from .utc import utcnow
from .upload import url_for_media
from .media_operations import store_file_from_url
from datetime import datetime
from settings import SERVER_DOMAIN
from uuid import uuid4
from eve.utils import config
from flask import abort, request, Response, current_app as app
from werkzeug.exceptions import NotFound
from superdesk import SuperdeskError
from superdesk.media_operations import resize_image
from werkzeug.datastructures import FileStorage
from PIL import Image
from superdesk.notification import push_notification
from superdesk.base_model import BaseModel


bp = superdesk.Blueprint('archive_media', __name__)
superdesk.blueprint(bp)


GUID_TAG = 'tag'
GUID_NEWSML = 'newsml'


class InvalidFileType(SuperdeskError):
    """Exception raised when receiving a file type that is not supported."""

    def __init__(self, type=None):
        super().__init__('Invalid file type %s' % type, payload={})


def on_create_item(docs):
    """Make sure item has basic fields populated."""
    for doc in docs:
        update_dates_for(doc)

        if not doc.get('guid'):
            doc['guid'] = generate_guid(type=GUID_NEWSML)

        doc.setdefault('_id', doc['guid'])


def update_dates_for(doc):
    for item in ['firstcreated', 'versioncreated']:
        doc.setdefault(item, utcnow())


def generate_guid(**hints):
    '''Generate a GUID based on given hints'''
    newsml_guid_format = 'urn:newsml:%(domain)s:%(timestamp)s:%(identifier)s'
    tag_guid_format = 'tag:%(domain)s:%(year)d:%(identifier)s'

    if not hints.get('id'):
        hints['id'] = str(uuid4())

    t = datetime.today()

    if hints['type'].lower() == GUID_TAG:
        return tag_guid_format % {'domain': SERVER_DOMAIN, 'year': t.year, 'identifier': hints['id']}
    elif hints['type'].lower() == GUID_NEWSML:
        return newsml_guid_format % {'domain': SERVER_DOMAIN, 'timestamp': t.isoformat(), 'identifier': hints['id']}
    return None


@bp.route('/archive_media/import_media/', methods=['POST'])
def import_media_into_archive():
    archive_guid = request.form['media_archive_guid']
    media_url = request.form['href']

    if request.form.get('rendition_name'):
        rendition_name = request.form['rendition_name']
        rv = import_rendition(archive_guid, rendition_name, media_url)
    else:
        rv = import_media(archive_guid, media_url)
    return Response(rv)


def import_media(media_archive_guid, href):
    '''
    media_archive_guid: media_archive guid
    href: external file URL from which to download it
    Download from href and save file on app storage, process it and
    update "original" rendition for guid content item
    '''
    rv = import_rendition(media_archive_guid, 'baseImage', href)
    return rv


def import_rendition(media_archive_guid, rendition_name, href):
    '''
    media_archive_guid: media_archive guid
    rendition_name: rendition to update,
    href: external file URL from which to download it
    Download from href and save file on app storage, process it and
    update "rendition_name" rendition for guid content item
    '''
    archive = fetch_media_from_archive(media_archive_guid)
    if rendition_name not in archive['renditions']:
        payload = 'Invalid rendition name %s' % rendition_name
        raise superdesk.SuperdeskError(payload=payload)

    file_guid = store_file_from_url(href)
    updates = {}
    updates['renditions'] = {rendition_name: {'href': url_for_media(file_guid)}}
    rv = superdesk.apps[ARCHIVE_MEDIA].update(id=str(media_archive_guid), updates=updates,
                                              trigger_events=True)
    return rv


def fetch_media_from_archive(media_archive_guid):
    print('Fetching media from archive with id=', media_archive_guid)
    archive = superdesk.apps[ARCHIVE_MEDIA].find_one(req=None, _id=str(media_archive_guid))
    if not archive:
        msg = 'No document found in the media archive with this ID: %s' % media_archive_guid
        raise superdesk.SuperdeskError(payload=msg)
    return archive


base_schema = {
    'guid': {
        'type': 'string',
        'unique': True
    },
    'provider': {
        'type': 'string'
    },
    'type': {
        'type': 'string',
        'required': True
    },
    'mimetype': {
        'type': 'string'
    },
    'version': {
        'type': 'string'
    },
    'versioncreated': {
        'type': 'datetime'
    },
    'pubstatus': {
        'type': 'string'
    },
    'copyrightholder': {
        'type': 'string'
    },
    'copyrightnotice': {
        'type': 'string'
    },
    'usageterms': {
        'type': 'string'
    },
    'language': {
        'type': 'string'
    },
    'place': {
        'type': 'list'
    },
    'subject': {
        'type': 'list'
    },
    'byline': {
        'type': 'string'
    },
    'headline': {
        'type': 'string'
    },
    'located': {
        'type': 'string'
    },
    'renditions': {
        'type': 'dict'
    },
    'slugline': {
        'type': 'string'
    },
    'creditline': {
        'type': 'string'
    },
    'description_text': {
        'type': 'string',
        'nullable': True
    },
    'firstcreated': {
        'type': 'datetime'
    },
    'filemeta': {
        'type': 'dict'
    },
    'ingest_provider': {
        'type': 'string'
    },
    'urgency': {
        'type': 'integer'
    },
    'groups': {
        'type': 'list'
    },
    'keywords': {
        'type': 'list'
    },
    'body_html': {
        'type': 'string'
    },
    'user': {
        'type': 'objectid',
        'data_relation': {
            'resource': 'users',
            'field': '_id',
            'embeddable': True
        }
    },
    'media_file': {
        'type': 'string'
    },
    'contents': {
        'type': 'list'
    },
    'media': {'type': 'media'}
}

ingest_schema = {
    'archived': {
        'type': 'datetime'
    }
}

planning_schema = {
    'scheduled': {
        'type': 'datetime'
    },
    'edNote': {
        'type': 'string'
    },
    'catalogRef': {
        'type': 'string'
    },
}

archive_schema = {}

ingest_schema.update(base_schema)
archive_schema.update(base_schema)
planning_schema.update(base_schema)

item_url = 'regex("[\w,.:-]+")'

extra_response_fields = ['guid', 'headline', 'firstcreated', 'versioncreated', 'archived']

facets = {
    'type': {'terms': {'field': 'type'}},
    'provider': {'terms': {'field': 'provider'}},
    'urgency': {'terms': {'field': 'urgency'}},
    'subject': {'terms': {'field': 'subject.name'}},
    'place': {'terms': {'field': 'place.name'}},
    'versioncreated': {'date_histogram': {'field': 'versioncreated', 'interval': 'hour'}},
}

ARCHIVE_MEDIA = 'archive_media'


def on_create_media_archive():
    push_notification('media_archive', created=1)


def on_update_media_archive():
    push_notification('media_archive', updated=1)


def on_delete_media_archive():
    push_notification('media_archive', deleted=1)


def on_create_planning():
    push_notification('planning', created=1)


def on_update_planning():
    push_notification('planning', updated=1)


def on_delete_planning():
    push_notification('planning', deleted=1)


def init_app(app):
    IngestModel(app=app)
    ArchiveModel(app=app)
    ArchiveMediaModel(app=app)
    PlanningModel(app=app)


class IngestModel(BaseModel):
    endpoint_name = 'ingest'
    schema = ingest_schema
    extra_response_fields = extra_response_fields
    item_url = item_url
    datasource = {
        'backend': 'elastic',
        'facets': facets
    }

    def on_create(self, docs):
        on_create_item(docs)
        on_create_media_archive()

    def on_update(self, updates, original):
        on_update_media_archive()

    def on_delete(self, doc):
        on_delete_media_archive()


class PlanningModel(BaseModel):
    endpoint_name = 'planning'
    schema = planning_schema
    extra_response_fields = extra_response_fields
    item_url = item_url
    datasource = {
        'backend': 'elastic',
        'facets': facets
    }
    resource_methods = ['GET', 'POST', 'DELETE']

    def on_create(self, docs):
        on_create_item(docs)
        on_create_planning()

    def on_update(self, updates, original):
        on_update_planning()

    def on_delete(self, doc):
        on_delete_planning()


class ArchiveModel(BaseModel):
    endpoint_name = 'archive'
    schema = archive_schema
    extra_response_fields = extra_response_fields
    item_url = item_url
    datasource = {
        'backend': 'elastic',
        'facets': facets
    }
    resource_methods = ['GET', 'POST', 'DELETE']

    def on_create(self, docs):
        on_create_item(docs)
        on_create_media_archive()

    def on_update(self, updates, original):
        on_update_media_archive()

    def on_delete(self, doc):
        '''Delete associated binary files.'''
        on_delete_media_archive()
        if doc and doc.get('renditions'):
            for _name, ref in doc['renditions'].items():
                try:
                    app.media.delete(ref['media'])
                except (KeyError, NotFound):
                    pass


class ArchiveMediaModel(BaseModel):
    type_av = {'image': 'picture', 'audio': 'audio', 'video': 'video'}
    endpoint_name = ARCHIVE_MEDIA
    schema = {
        'media': {
            'type': 'media',
            'required': True
        },
        'upload_id': {'type': 'string'},
        'headline': base_schema['headline'],
        'byline': base_schema['byline'],
        'description_text': base_schema['description_text']
    }
    datasource = {'source': 'archive'}
    resource_methods = ['POST']
    item_methods = ['PATCH', 'GET', 'DELETE']
    item_url = item_url

    def on_update(self, updates, original):
        on_update_media_archive()

    def on_delete(self, doc):
        on_delete_media_archive()

    def on_create(self, docs):
        ''' Create corresponding item on file upload '''
        for doc in docs:
            file = self.get_file_from_document(doc)
            inserted = [doc['media']]
            file_type = file.content_type.split('/')[0]

            try:
                update_dates_for(doc)
                doc['guid'] = generate_guid(type=GUID_TAG)
                doc['type'] = self.type_av.get(file_type)
                doc['version'] = 1
                doc['versioncreated'] = utcnow()
                doc['renditions'] = self.generate_renditions(file, doc['media'], inserted, file_type)
                doc['mimetype'] = file.content_type
                doc['filemeta'] = file.metadata
            except Exception as io:
                superdesk.logger.exception(io)
                for file_id in inserted:
                    self.delete_file_on_error(doc, file_id)
                abort(500)
        on_create_media_archive()

    def get_file_from_document(self, doc):
        file = doc.get('media_fetched')
        if not file:
            file = app.media.get(doc['media'])
        else:
            del doc['media_fetched']
        return file

    def delete_file_on_error(self, doc, file_id):
        # Don't delete the file if we are on the import from storage flow
        if doc['_import']:
            return
        app.media.delete(file_id)

    def generate_renditions(self, original, media_id, inserted, file_type):
        """Generate system renditions for given media file id."""
        rend = {'href': url_for_media(media_id), 'media': media_id, 'mimetype': original.content_type}
        renditions = {'original': rend}

        if file_type != 'image':
            return renditions

        img = Image.open(original)
        width, height = img.size
        rend.update({'width': width})
        rend.update({'height': height})

        ext = original.content_type.split('/')[1].lower()
        ext = ext if ext in ('jpeg', 'gif', 'tiff', 'png') else 'png'
        for rendition, rsize in config.RENDITIONS['picture'].items():
            size = (rsize['width'], rsize['height'])
            original.seek(0)
            resized, width, height = resize_image(original, ext, size)
            resized = FileStorage(stream=resized, content_type='image/%s' % ext)
            id = superdesk.app.media.put(resized)
            inserted.append(id)
            renditions[rendition] = {'href': url_for_media(id), 'media': id,
                                     'mimetype': 'image/%s' % ext, 'width': width, 'height': height}
        return renditions
