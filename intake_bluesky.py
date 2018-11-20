import collections
import intake.catalog
import intake.catalog.entry
import intake.source.base
import itertools
import math
import pandas
import pymongo
import pymongo.errors


class FacilityCatalog(intake.catalog.Catalog):
    "spans multiple MongoDB instances"
    ...


class MongoMetadataStoreCatalog(intake.catalog.Catalog):
    "represents one MongoDB instance"
    def __init__(self, uri, *, query=None, **kwargs):
        # All **kwargs are passed up to base class. TODO: spell them out
        # explicitly.
        self._uri = uri
        self._query = query
        self._client = pymongo.MongoClient(uri)
        kwargs.setdefault('name', uri)
        try:
            # Called with no args, get_database() returns the database
            # specified in the uri --- or raises if there was none. There is no
            # public method for checking this in advance, so we just catch the
            # error.
            db = self._client.get_database()
        except pymongo.errors.ConfigurationError as err:
            raise ValueError(
                "Invalid uri. Did you forget to include a database?") from err
        self._run_start_collection = db.get_collection('run_start')
        self._run_stop_collection = db.get_collection('run_stop')
        self._event_descriptor_collection = db.get_collection('event_descriptor')
        self._event_collection = db.get_collection('event')
        self._query = query or {}
        super().__init__(**kwargs)
        # Note: We don't do anything with self._entries, which is defined by
        # the base class, because we fetch our entries lazily rather than
        # caching them in a dict.

    def _get_entry(self, name):
        query = {'$and': [self._query, {'uid': name}]}
        run_start_doc = self._run_start_collection.find_one(query)
        del run_start_doc['_id']  # Drop internal Mongo detail.
        return RunCatalog(run_start_doc=run_start_doc,
                          run_stop_collection=self._run_stop_collection,
                          event_descriptor_collection=self._event_descriptor_collection,
                          event_collection=self._event_collection)

    def __iter__(self):
        cursor = self._run_start_collection.find(
            self._query, sort=[('time', pymongo.DESCENDING)])
        for run_start_doc in cursor:
            yield (run_start_doc['uid'],
                   RunCatalog(run_start_doc=run_start_doc,
                              run_stop_collection=self._run_stop_collection,
                              event_descriptor_collection=self._event_descriptor_collection,
                              event_collection=self._event_collection))

    def _close(self):
        self._client.close()

    def search(self, query, depth=1):
        if self.query:
            query = {'$and': [self._query, query]}
        cat = MetadataStoreCatalog(
            uri=self.uri,
            query=query,
            getenv=self.getenv,
            getshell=self.getshell,
            auth=self.auth,
            metadata=(self.metadata or {}).copy(),
            storage_options=self.storage_options)
        cat.metadata['search'] = {'query': query, 'upstream': self.name}
        return cat


class RunCatalog(intake.catalog.Catalog):
    "represents one MongoDB instance"
    def __init__(self, *,
                 run_start_doc,
                 run_stop_collection,
                 event_descriptor_collection,
                 event_collection,
                 **kwargs):
        # All **kwargs are passed up to base class. TODO: spell them out
        # explicitly.
        self._run_start_doc = run_start_doc
        self._run_stop_collection = run_stop_collection
        self._event_descriptor_collection = event_descriptor_collection
        self._event_collection = event_collection
        super().__init__(**kwargs)

    def _load(self):
        uid = self._run_start_doc['uid']
        run_stop_doc = self._run_stop_collection.find_one({'run_start': uid})
        del run_stop_doc['_id']  # Drop internal Mongo detail.
        self.metadata.update(self._run_start_doc)
        self.metadata.update(run_stop_doc)
        cursor = self._event_descriptor_collection.find(
            {'run_start': uid},
            sort=[('time', pymongo.ASCENDING)])
        # Sort descriptors like
        # {'stream_name': [descriptor1, descriptor2, ...], ...}
        streams = itertools.groupby(cursor,
                                    key=lambda d: d.get('name'))
        # Make a MongoEventStreamSource for each stream_name.
        self._entries = {
            stream_name: MongoEventStream(self._run_start_doc,
                                          list(event_descriptor_docs),
                                          self._event_collection,
                                          self._run_stop_collection)
            for stream_name, event_descriptor_docs in streams}


class MongoEventStream(intake.catalog.Catalog):
    container = 'dataframe'
    name = 'foo'
    version = '0.0.1'
    partition_access = True

    def __init__(self, run_start_doc, event_descriptor_docs, event_collection,
                 run_stop_collection, metadata=None):
        self._partition_size = 100
        self._default_chunks = 100
        self._run_start_doc = run_start_doc
        self._event_collection = event_collection
        self._event_descriptor_docs = event_descriptor_docs
        self._run_stop_collection = run_stop_collection
        super().__init__(
            metadata=metadata
        )

    def _load(self):
        # Make a MongoEventStreamSource for each stream_name.
        self._entries = {
            field: MongoField(field,
                              self._event_descriptor_docs,
                              self._event_collection)
            for field in list(self._event_descriptor_docs[0]['data_keys'])}

    def _get_schema(self):
        return intake.source.base.Schema(
            datashape=None,
            dtype={'x': "int64", 'y': "int64"},
            shape=(None, 2),
            npartitions=2,
            extra_metadata=dict(c=3, d=4)
        )

    def read_slice(self, slice_):
        if isinstance(slice_, collections.Iterable):
            first_axis = slice_[0]
        else:
            first_axis = slice_
        query = {'descriptor': {'$in': [d['uid'] for d in self._event_descriptor_docs]}}
        seq_num_filter = {}
        if first_axis.start is not None:
            seq_num_filter['$gte'] = first_axis.start
        if first_axis.stop is not None:
            seq_num_filter['$lt'] = first_axis.stop
        if first_axis.step is not None:
            # Have to think about how this interacts with repeated seq_num.
            raise NotImplementedError
        if seq_num_filter:
            query['seq_num'] = seq_num_filter
        cursor = self._event_collection.find(
            query,
            sort=[('descriptor', pymongo.DESCENDING),
                  ('time', pymongo.ASCENDING)])

        events = list(cursor)
        seq_nums = [ev['seq_num'] for ev in events]
        times = [ev['time'] for ev in events]
        # uids = [ev['uid'] for ev in events]
        keys = list(self._event_descriptor_docs[0]['data_keys'])
        data_table = _transpose(events, keys, 'data')
        # timestamps_table = _transpose(all_events, keys, 'timestamps')
        # data_keys = descriptor['data_keys']
        # external_keys = [k for k in data_keys if 'external' in data_keys[k]]
        return pandas.DataFrame({'time': times, **data_table}, index=seq_nums)

    def read(self):
        return self.read_slice(slice(None))

    def read_chunked(self, chunks=None):
        if chunks is None:
            chunks = self._default_chunks
        for i in itertools.count():
            # Start at 1 because seq_num starts at 1 ugh....
            partition = self.read_slice(
                slice(1 + i * chunks, 1 + (i + 1) * chunks))
            if len(partition):
                yield partition
            else:
                break

    def _close(self):
        pass


class MongoField(intake.source.base.DataSource):
    container = 'dataframe'
    name = 'foo'
    version = '0.0.1'
    partition_access = True

    def __init__(self, field, event_descriptor_docs, event_collection,
                 metadata=None):
        self._partition_size = 100
        self._default_chunks = 100
        self._field = field
        self._event_collection = event_collection
        self._event_descriptor_docs = event_descriptor_docs
        super().__init__(
            metadata=metadata
        )

    def _get_schema(self):
        return intake.source.base.Schema(
            datashape=None,
            dtype={'x': "int64", 'y': "int64"},
            shape=(None, 2),
            npartitions=2,
            extra_metadata=dict(c=3, d=4)
        )

    def read_slice(self, slice_):
        if isinstance(slice_, collections.Iterable):
            first_axis = slice_[0]
        else:
            first_axis = slice_
        query = {'descriptor': {'$in': [d['uid'] for d in self._event_descriptor_docs]}}
        seq_num_filter = {}
        if first_axis.start is not None:
            seq_num_filter['$gte'] = first_axis.start
        if first_axis.stop is not None:
            seq_num_filter['$lt'] = first_axis.stop
        if first_axis.step is not None:
            # Have to think about how this interacts with repeated seq_num.
            raise NotImplementedError
        if seq_num_filter:
            query['seq_num'] = seq_num_filter
        cursor = self._event_collection.find(
            query,
            sort=[('descriptor', pymongo.DESCENDING),
                  ('time', pymongo.ASCENDING)])

        events = list(cursor)
        seq_nums = [ev['seq_num'] for ev in events]
        times = [ev['time'] for ev in events]
        # uids = [ev['uid'] for ev in events]
        data_keys = self._event_descriptor_docs[0]['data_keys']
        data_table = _transpose(events, data_keys, 'data')
        if data_keys[self._field]['dtype'] != 'array':
            return pandas.Series(data_table[self._field], index=seq_nums)
        else:
             raise NotImplementedError
        # timestamps_table = _transpose(all_events, keys, 'timestamps')
        # data_keys = descriptor['data_keys']
        # external_keys = [k for k in data_keys if 'external' in data_keys[k]]

    def read_chunked(self, chunks=None):
        if chunks is None:
            chunks = self._default_chunks
        for i in itertools.count():
            # Start at 1 because seq_num starts at 1 ugh....
            partition = self.read_slice(
                slice(1 + i * chunks, 1 + (i + 1) * chunks))
            if len(partition):
                yield partition
            else:
                break

    def read(self):
        return self.read_slice(slice(None))

    def _close(self):
        pass


def _transpose(in_data, keys, field):
    """Turn a list of dicts into dict of lists

    Parameters
    ----------
    in_data : list
        A list of dicts which contain at least one dict.
        All of the inner dicts must have at least the keys
        in `keys`

    keys : list
        The list of keys to extract

    field : str
        The field in the outer dict to use

    Returns
    -------
    transpose : dict
        The transpose of the data
    """
    out = {k: [None] * len(in_data) for k in keys}
    for j, ev in enumerate(in_data):
        dd = ev[field]
        for k in keys:
            out[k][j] = dd[k]
    return out
