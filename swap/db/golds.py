
from swap.db.db import Collection
import swap.utils.parsers as parsers
import swap.config as config

import csv
import sys
from functools import wraps
from pymongo import IndexModel, ASCENDING

import logging
logger = logging.getLogger(__name__)


def parse_golds(func):
    """
    Generates subject to gold mapping from a cursor

    Iterates through a cursor and parses out the subject to gold
    mappings.

    Parameters
    ----------
    cursor : swap.db.Cursor
        cursor containing data. Should be an aggregation containing
        one document per subject, and with the fields _id mapped to
        subject_id and gold mapped to the appropriate gold label.
    type_ : return type
        Choose the return type. Choices are:

        dict
            {_id : gold}
        tuple
            (_id, gold)
    """

    @wraps(func)
    def wrapper(self, *args, type_=dict, **kwargs):
        # build cursor from query
        query = self._pre()
        query += func(self, *args, **kwargs)
        query += self._post()

        cursor = self.aggregate(query, debug_query=False)

        if type_ is dict:
            data = {}
            for item in cursor:
                id_ = item['subject']
                gold = item['gold']

                data[id_] = gold
        elif type_ is tuple:
            data = []
            for item in cursor:
                data.append((item['subject'], item['gold']))
        else:
            raise TypeError("type_ '%s' invalid type!" % str(type_))

        return data
    return wrapper


class Golds(Collection):

    @staticmethod
    def _collection_name():
        return 'subjects'

    @staticmethod
    def _schema():
        return parsers.GoldsParser(None).config

    def _init_collection(self):
        indexes = [
            IndexModel([('gold', ASCENDING)]),
            IndexModel([('subject', ASCENDING)])
        ]

        logger.debug('inserting %d indexes', len(indexes))
        self.collection.create_indexes(indexes)
        logger.debug('done')

    #######################################################################

    @staticmethod
    def _pre():
        return [
            {'$match': {'gold': {'$exists': 1}}}
        ]

    @staticmethod
    def _post():
        return [
            {'$project': {'subject': 1, 'gold': 1}}
        ]

    @parse_golds
    def get_golds(self, subjects=None):
        query = []
        if subjects is not None:
            query += [{'$match': {'subject': {'$in': subjects}}}]

        return query

    @parse_golds
    def get_random_golds(self, size, gold_filter=None):
        if gold_filter is not None:
            query = [{'$match': {'gold': gold_filter}}]
        else:
            query = [{'$match': {'gold': {'$ne': -1}}}]
        query += [{'$sample': {'size': size}}]

        return query

    def build_from_classifications(self):
        query = [
            {'$group': {'_id': '$subject_id',
                        'gold': {'$last': '$gold_label'}}},
            {'$project': {'subject': '$_id', 'gold': 1, '_id': 0}},
            {'$out': self._collection_name()}
        ]

        logger.critical('building gold label list from classifications')
        self._db.classifications.aggregate(query)

    def update(self, subject, gold, upsert=False):
        self.collection.update_many(
            {'subject': subject}, {'$set': {'gold': gold}}, upsert=upsert)

    def upload_golds_csv(self, fname):
        logger.info('parsing csv dump')
        self._init_collection()

        insert = []
        subjects = self._db.subjects.get_subjects()
        print(subjects)
        pp = parsers.GoldsParser('csv')

        with open(fname, 'r') as file:
            reader = csv.DictReader(file)

            for i, row in enumerate(reader):
                item = pp.process(row)
                if item is None:
                    continue

                if item['subject'] in subjects:
                    self.update(item['subject'], item['gold'])
                else:
                    insert.append(item)

                if i % 100 == 0:
                    sys.stdout.flush()
                    sys.stdout.write("%d records processed\r" % i)

                if len(insert) > 100000:
                    self.collection.insert_many(insert)
                    insert = []

        self.collection.insert_many(insert)
        logger.debug('done')
