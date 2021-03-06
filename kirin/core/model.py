# Copyright (c) 2001-2015, Canal TP and/or its affiliates. All rights reserved.
#
# This file is part of Navitia,
#     the software to build cool stuff with public transport.
#
# Hope you'll enjoy and contribute to this project,
#     powered by Canal TP (www.canaltp.fr).
# Help us simplify mobility and open public transport:
#     a non ending quest to the responsive locomotion way of traveling!
#
# LICENCE: This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# Stay tuned using
# twitter @navitia
# IRC #navitia on freenode
# https://groups.google.com/d/forum/navitia
# www.navitia.io

from sqlalchemy.dialects import postgresql
from flask_sqlalchemy import SQLAlchemy
import datetime
import sqlalchemy
db = SQLAlchemy()

# default name convention for db constraints (when not specified), for future alembic updates
meta = sqlalchemy.schema.MetaData(naming_convention={
        "ix": 'ix_%(column_0_label)s',
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s"
      })

#force the server to use UTC time for each connection
def set_utc_on_connect(dbapi_con, con_record):
    c = dbapi_con.cursor()
    c.execute("SET timezone='utc'")
    c.close()
sqlalchemy.event.listen(sqlalchemy.pool.Pool, 'connect', set_utc_on_connect)


def gen_uuid():
    """
    Generate uuid as string
    """
    import uuid
    return str(uuid.uuid4())


class TimestampMixin(object):
    created_at = db.Column(db.DateTime(), default=datetime.datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(), default=None, onupdate=datetime.datetime.utcnow)

ModificationType = db.Enum('add', 'delete', 'update', 'none', name='modification_type')


class VehicleJourney(db.Model):
    """
    Vehicle Journey
    """
    id = db.Column(postgresql.UUID, default=gen_uuid, primary_key=True)
    navitia_id = db.Column(db.Text, nullable=False)
    circulation_date = db.Column(db.Date, nullable=False)

    __table_args__ = (db.UniqueConstraint('navitia_id', 'circulation_date', name='vehicle_journey_navitia_id_circulation_date_idx'),)

    def __init__(self, navitia_vj, circulation_date):
        self.id = gen_uuid()
        self.navitia_id = navitia_vj['id']
        self.circulation_date = circulation_date
        self.navitia_vj = navitia_vj  # Not persisted


class StopTimeUpdate(db.Model, TimestampMixin):
    """
    Stop time
    """
    id = db.Column(postgresql.UUID, default=gen_uuid, primary_key=True)
    trip_update_id = db.Column(postgresql.UUID, db.ForeignKey('trip_update.vj_id'), nullable=False)

    stop_id = db.Column(db.Text, nullable=False)

    departure = db.Column(db.DateTime, nullable=True)
    departure_status = db.Column(ModificationType, nullable=False, default='none')

    arrival = db.Column(db.DateTime, nullable=True)
    arrival_status = db.Column(ModificationType, nullable=False, default='none')

    def __init__(self, navitia_stop, departure, arrival):
        self.id = gen_uuid()
        self.navitia_stop = navitia_stop
        self.stop_id = navitia_stop['id']
        self.departure = departure
        self.arrival = arrival
        self.departure_status = 'none'
        self.arrival_status = 'none'

    def merge(self, other):
        if not other:
            return
        #TODO diff can be at departure or arrival
        assert self.stop_id == other.stop_id
        self.departure = other.departure
        self.departure_status = other.departure_status
        self.arrival = other.arrival
        self.arrival_status = other.arrival_status



associate_realtimeupdate_tripupdate = db.Table('associate_realtimeupdate_tripupdate',
                                    db.metadata,
                                    db.Column('real_time_update_id', postgresql.UUID, db.ForeignKey('real_time_update.id')),
                                    db.Column('trip_update_id', postgresql.UUID, db.ForeignKey('trip_update.vj_id')),
                                    db.PrimaryKeyConstraint('real_time_update_id', 'trip_update_id', name='associate_realtimeupdate_tripupdate_pkey')
)


class TripUpdate(db.Model, TimestampMixin):
    """
    Update information for Vehicule Journey
    """
    vj_id = db.Column(postgresql.UUID, db.ForeignKey('vehicle_journey.id'), nullable=False, primary_key=True)
    status = db.Column(ModificationType, nullable=False, default='none')
    vj = db.relationship('VehicleJourney', backref='trip_update', uselist=False)
    stop_time_updates = db.relationship('StopTimeUpdate', backref='trip_update', lazy='joined',
                                        cascade='all, delete-orphan')

    def __init__(self, vj=None):
        self.created_at = datetime.datetime.utcnow()
        self.vj = vj
        self.status = 'none'

    @classmethod
    def find_by_dated_vj(cls, vj_navitia_id, vj_circulation_date):
        return cls.query.join(VehicleJourney).filter(VehicleJourney.navitia_id == vj_navitia_id,
                                              VehicleJourney.circulation_date == vj_circulation_date).first()

    def find_stop(self, stop_id):
        #TODO: we will need to handle vj who deserve the same stop multiple times
        for st in self.stop_time_updates:
            if st.stop_id == stop_id:
                return st
        return None

    def merge(self, other):
        if not other:
            return
        for stop in other.stop_time_updates:
            current_stop = self.find_stop(stop.stop_id)
            current_stop.merge(stop)
        self.status = other.status



class RealTimeUpdate(db.Model, TimestampMixin):
    """
    Real Time Update received from POST request

    This model is used to persist the raw_data: .
    A real time update object will be constructed from the raw_xml then the
    constructed real_time_update's id should be affected to TripUpdate's real_time_update_id

    There is a one-to-many relationship between RealTimeUpdate and TripUpdate.
    """
    id = db.Column(postgresql.UUID, default=gen_uuid, primary_key=True)
    received_at = db.Column(db.DateTime, nullable=False)
    contributor = db.Column(db.Text, nullable=True)
    connector = db.Column(db.Enum('ire', 'gtfs-rt', name='connector_type'), nullable=False)
    status = db.Column(db.Enum('OK', 'KO', 'pending', name='rt_status'), nullable=True)
    error = db.Column(db.Text, nullable=True)
    raw_data = db.Column(db.Text, nullable=True)

    trip_updates = db.relationship("TripUpdate", secondary=associate_realtimeupdate_tripupdate, backref='real_time_updates')

    def __init__(self, raw_data, connector,
                 contributor=None, status=None, error=None, received_at=datetime.datetime.now()):
        self.id = gen_uuid()
        self.raw_data = raw_data
        self.contributor = contributor
        self.connector = connector
        self.status = status
        self.error = error
        self.received_at = received_at
