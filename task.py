import logging
from flask import Flask, request
from werkzeug.exceptions import BadRequest, MethodNotAllowed
from IMDB_retriever import retrieve_movie_from_title
from google.appengine.api import taskqueue
from datetime import datetime
from google.appengine.ext import ndb
from gcm import GCM
from models import Artist, Movie, TasteArtist, TasteMovie, TasteGenre
from models import User
from utilities import TV_TYPE, GENRES, ACTOR_WEIGHT, DIRECTOR_WEIGHT, WRITER_WEIGHT, GENRE_WEIGHT

from movie_selector import random_movie_selection
from send_mail import send_suggestion
from tv_scheduling import result_movies_schedule
from utilities import TV_TYPE, RetrieverError

app = Flask(__name__)
app.config['DEBUG'] = True


@app.route('/_ah/start/task/retrieve/<time>')
def retrieve(time):
    """
    Retrieve movie info from IMDB using taskqueue for all movies from "today" schedule.
    :return: simple confirmation string
    :rtype string
    """
    taskqueue.add(url='/_ah/start/task/retrieve/free/' + time, method='GET')
    taskqueue.add(url='/_ah/start/task/retrieve/sky/' + time, method='GET')
    taskqueue.add(url='/_ah/start/task/retrieve/premium/' + time, method='GET')

    return 'OK'


@app.route('/_ah/start/task/suggest')
def suggest():
    """
    :return: simple confirmation string
    :rtype string
    """
    taskqueue.add(url='/_ah/start/task/proposal', method='GET')
    return 'OK'


@app.route('/_ah/start/task/clear')
def clear():
    """
    :return: simple confirmation string
    :rtype string
    """
    taskqueue.add(url='/_ah/start/task/proposal', method='DELETE')
    return 'OK'


@app.route('/_ah/start/task/proposal', methods=['GET', 'DELETE'])
def proposal():
    """
    :return: simple confirmation string
    :rtype string
    """
    if request.method == 'GET':
        users = User.query()
        for user in users:
            taskqueue.add(url='/api/proposal/' + user.key.id(), method='GET')
    elif request.method == 'DELETE':
        users = User.query()
        for user in users:
            user.proposal = None
            user.put()
    else:
        raise MethodNotAllowed
    return 'OK'


@app.route('/_ah/start/task/retrieve/<tv_type>/<day>', methods=['GET'])
def retrieve_type(tv_type, day):
    """
    Retrieve movie info from IMDB for all movies from day schedule by tv type.
    :return: simple confirmation string
    :rtype string
    """

    if tv_type in TV_TYPE:
        movies = result_movies_schedule(tv_type, day)  # Retrieve movies from today schedule
        while len(movies) > 0:
            movie_title = movies[0]['title']
            movie_original_title = movies[0]['originalTitle']
            movie_year = movies[0]['year']
            movie_genre = movies[0]['genres']
            movie_director = movies[0]['director']
            movie_cast = movies[0]['cast']

            if movie_original_title is None:
                movie_original_title = movie_title

            try:
                retrieve_movie_from_title(movie_original_title,
                                          movie_director,
                                          movie_cast,
                                          movie_title,
                                          movies[0]['movieUrl'],
                                          movie_year,
                                          movie_genre)  # Retrieve movie from IMDB(or not) by title and year and store it
                movies.pop(0)
            except Exception as exception:
                logging.error("Error in retrieving %s: %s", movie_original_title, exception)
                if type(exception) is RetrieverError:
                    logging.error("Not our error...")
                movies.pop(0)
                pass
        return 'OK'
    else:
        raise BadRequest


@app.route('/_ah/start/task/manual/<offset>')
def manual(offset):
    """
    Useful function
    :return: simple confirmation string
    :rtype string
    """
    taskqueue.add(url='/api/manual/' + offset, method='GET')

    return 'OK'


@app.route('/_ah/start/task/notification')
def notification():

    logging.info("Started push notification control " + str(datetime.now().hour) + ":" + str(datetime.now().minute))
    time_in_ms = (datetime.now().hour + 1)*3600000 + datetime.now().minute*60000 + datetime.now().second*1000
    users = User.query()

    for i in users:
        if i.time_notification <= time_in_ms + 300000 and i.time_notification >= time_in_ms - 300000 and i.key.id() != "vintilaniculina94@gmail.com":
            taskqueue.add(url='/_ah/start/task/push/'+i.key.id(), method='GET')
    return "OK"


@app.route('/_ah/start/task/push/<user_id>', methods=['GET'])
def push(user_id):
    # movies = Movie.query()
    # for movie in movies.fetch(500, offset=int(offset)):
    #     movie.poster = clear_url(movie.poster)
    #     movie.put()
    # artists = Artist.query()
    # for artist in artists.fetch(500, offset=int(offset)):
    #     artist.photo = clear_url(artist.photo)
    #     artist.put()

    user = User.get_by_id(user_id)
    data = "PRIMETIME4U ha nuove proposte per te"
    gcm = GCM("AIzaSyAPfZ91t379OWzTiyALsInNnYsWhemF_o0")
    data = {'message': data}

    reg_id = user.gcm_key

    if reg_id is not None and user.enable_notification is not False:
        gcm.plaintext_request(registration_id=reg_id, data=data)

    return 'OK'

@app.route('/_ah/start/task/movie_tastes/<user_id>/<movie_id>/<taste>', methods=['GET'])
def add_movie_tastes(user_id, movie_id, taste):

        taste = float(taste)
        user = User.get_by_id(user_id)
        movie = Movie.get_by_id(movie_id)
        for actor in movie.actors:
            artist = Artist.get_by_id(actor.id())
            user.add_taste_artist(artist, ACTOR_WEIGHT * taste)

        for director in movie.directors:
            artist = Artist.get_by_id(director.id())
            user.add_taste_artist(artist, DIRECTOR_WEIGHT * taste)

        for writer in movie.writers:
            artist = Artist.get_by_id(writer.id())
            user.add_taste_artist(artist, WRITER_WEIGHT * taste)

        for genre in movie.genres:
            user.add_taste_genre(genre, GENRE_WEIGHT * taste)

        user.remove_proposal()
        user.put()
        taskqueue.add(url='/api/proposal/' + user.key.id(), method='GET')


        return 'OK'

@app.route('/_ah/start/task/movie_untaste/<user_id>/<movie_id>', methods=['GET'])
def untaste_movie(user_id, movie_id):
            movie = Movie.get_by_id(movie_id)
            user = User.get_by_id(user_id)

            for actor in movie.actors:
                artist = Artist.get_by_id(actor.id())
                taste_artist = TasteArtist.get_by_id(actor.id() + user.key.id())

                if taste_artist is not None:
                    taste_artist.update_taste(-ACTOR_WEIGHT)
                else:
                    user.add_taste_artist(artist, -ACTOR_WEIGHT)

                if taste_artist.taste == 0:
                    user.remove_taste_artist(artist)

            for director in movie.directors:
                artist = Artist.get_by_id(director.id())
                taste_artist = TasteArtist.get_by_id(director.id() + user.key.id())

                if taste_artist is not None:
                    taste_artist.update_taste(-DIRECTOR_WEIGHT)
                else:
                    user.add_taste_artist(artist, -DIRECTOR_WEIGHT)

                if taste_artist.taste == 0:
                    user.remove_taste_artist(artist)

            for writer in movie.writers:
                artist = Artist.get_by_id(writer.id())
                taste_artist = TasteArtist.get_by_id(writer.id() + user.key.id())

                if taste_artist is not None:
                    taste_artist.update_taste(-WRITER_WEIGHT)
                else:
                    user.add_taste_artist(artist, -WRITER_WEIGHT)

                if taste_artist.taste == 0:
                    user.remove_taste_artist(artist)

            for genre in movie.genres:
                taste_genre = TasteGenre.get_by_id(genre + user.key.id())

                if taste_genre is not None:
                    taste_genre.update_taste(-GENRE_WEIGHT)
                else:
                    user.add_taste_genre(genre, -GENRE_WEIGHT)

                if taste_genre.taste == 0:
                    user.remove_taste_genre(genre)

            user.remove_proposal()
            user.put()
            taskqueue.add(url='/api/proposal/' + user.key.id(), method='GET')

            return 'OK'
