from django.db import models
from django.db.models import Q
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.base_user import BaseUserManager
import datetime
from django.utils import timezone
from musicroom.settings import STORAGE_URLS
from musicroom.common import makecode, live_event, roomtask, usertask, dump_datetime, schedule


class UserManager(BaseUserManager):
    """
    Custom user model manager where email is the unique identifiers
    for authentication instead of usernames.
    """

    def create_user(self, email, password, **extra_fields):
        """
        Create and save a User with the given email and password.
        """
        if not email:
            raise ValueError('The Email must be set')
        email = self.normalize_email(email)
        extra_fields.setdefault('name', email.split('@')[0])
        user = self.model(
            email=email, last_seen=timezone.now(), **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password, **extra_fields):
        """
        Create and save a SuperUser with the given email and password.
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff = True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser = True.')
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(
        verbose_name='Email',
        max_length=255,
        unique=True,
    )
    name = models.CharField(max_length=100)
    avatar_url = models.CharField(max_length=250, null=True, default=None)
    first_name = None
    last_name = None
    last_seen = models.DateTimeField()
    room_joined_on = models.DateTimeField(null=True, default=None)
    room = models.ForeignKey(
        'Room', on_delete=models.SET_NULL, related_name="members", null=True, default=None)
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def get_value(self, field_name):
        field_object = User._meta.get_field(field_name)
        value = field_object.value_from_object(self)
        return value

    def broadcast(self, msg_type, **data):
        grp_id = 'user-'+str(self.id)
        live_event(group=grp_id, msg_type=msg_type, **data)

    def seen_now(self, save=True):
        self.last_seen = timezone.now()
        if save:
            self.save()

    def get_friends(self):
        # returns both catagory 3 and 1
        friends = Friendship.objects.filter(
            Q(user1=self) | Q(user2=self, is_accepted=True))
        List = []
        for friend in friends:
            if friend.user1 == self:
                List.append(friend.user2)
            else:
                List.append(friend.user1)
        return List

    def get_friend_requests(self):
        requests = Friendship.objects.filter(user2=self, is_accepted=False)
        List = []
        for request in requests:
            List.append(request.user1)
        return List

    def make_friend(self, user):
        try:
            return Friendship.create(self, user)
        except:
            try:
                friend = Friendship.objects.get(
                    user1=user, user2=self, is_accepted=False)
            except:
                raise ValueError("Already a friend or requested")
            else:
                friend.accept(save=True)
                return friend

    def unfriend(self, user):
        code, friend = self.friendship_status(user)
        if code > 0:
            friend.remove()
            return True
        else:
            return False

    def friendship_status(self, user):
        friends = Friendship.objects.filter(
            Q(user1=self, user2=user) | Q(user2=self, user1=user))
        if len(friends):
            if friends[0].is_accepted:
                return (3, friends[0])
            else:
                if friends[0].user1 == self:
                    return (1, friends[0])
                else:
                    return (2, friends[0])
        else:
            return (0, None)

    def get_profile_min(self):
        return {'user_id': self.id, 'name': self.name, 'avatar_url': self.avatar_url}

    def get_profile(self, ref_user):
        profile = {'user_id': self.id, 'name': self.name,
                   'avatar_url': self.avatar_url, 'is_self': True}
        if self != ref_user:
            profile['is_self'] = False
            friend_status, friend_obj = ref_user.friendship_status(self)
            profile['friendship_status'] = friend_status
            if friend_status == 3:
                profile['friends_since'] = friend_obj.accepted_on
                profile['score'] = friend_obj.score
                profile['common_time'] = friend_obj.common_time
                if self.room != None:
                    profile['room'] = self.room.get_title_obj(ref_user)
        else:
            profile['email'] = self.email
        return profile

    def create_room(self, tracks):
        room = Room.create(tracks)
        room.grant_access(self)
        self.join_room(room)
        return room

    def join_room(self, room):
        self.leave_room()
        if room.can_user_access(self):
            self.room = room
            self.save()
            usertask('room.join', self.id, room_id=room.get_value('id'))
            room.broadcast('update.members.add',
                           action_user=self.get_profile_min())
            return room
        else:
            raise ValueError("User does not have access")

    def leave_room(self):
        if self.room != None:
            room = self.room
            self.room = None
            self.save()
            usertask('room.leave', self.id, room_id=room.get_value('id'))
            room.broadcast('update.members.remove',
                           action_user=self.get_profile_min())
            if room.members.count() == 0:
                room.dissolve()

    def get_rooms(self):
        max_rooms = 10
        rooms = []
        friends = self.get_friends()
        for friend in friends:
            if friend.room != None and friend.room.can_user_access(self):
                rooms.append(friend.room)
                if len(rooms) >= max_rooms:
                    break
        return rooms

    def __str__(self):
        return self.email

    @classmethod
    def get_by_id(cls, pk):
        return cls.objects.get(id=pk)


class Friendship(models.Model):
    user1 = models.ForeignKey(
        User, related_name="user1_set", on_delete=models.CASCADE)
    user2 = models.ForeignKey(
        User, related_name="user2_set", on_delete=models.CASCADE)
    init_on = models.DateTimeField()
    is_accepted = models.BooleanField()
    accepted_on = models.DateTimeField(null=True, default=None)
    score = models.IntegerField()
    common_time = models.TimeField()

    def remove(self):
        self.delete()

    def accept(self, save=False):
        if not self.is_accepted:
            self.is_accepted = True
            self.accepted_on = timezone.now()
        if save:
            self.save()

    class Meta:
        unique_together = ['user1', 'user2']

    @classmethod
    def create(cls, user1, user2):
        existing = cls.objects.filter(
            Q(user1=user1, user2=user2) | Q(user1=user2, user2=user1)).count()
        if existing == 0:
            obj = cls(user1=user1, user2=user2, init_on=timezone.now(),
                      is_accepted=False, score=0, common_time=datetime.time(0, 0, 0))
            obj.save()
            return obj
        else:
            raise ValueError("Friendship obj already exists for these users")

    def __str__(self):
        return self.user1.name+' & '+self.user2.name


class Room(models.Model):
    created_on = models.DateTimeField()
    last_check_on = models.DateTimeField()
    access_users = models.ManyToManyField(
        User, related_name="access_to_rooms")
    is_paused = models.BooleanField(default=False)
    paused_on = models.DateTimeField(null=True, default=None)
    duration_to_complete = models.TimeField()
    play_start_time = models.DateTimeField()
    no_tracks = models.IntegerField(default=0)
    code = models.CharField(max_length=50, default=None, null=True)
    current_roomtrack = models.ForeignKey(
        "RoomTrack", on_delete=models.CASCADE, related_name="+")

    def check_state(self):
        curr_time = timezone.now()
        offline_members = self.members.filter(
            last_seen__lte=curr_time-datetime.timedelta(seconds=0,  minutes=5))
        for user in offline_members:
            user.leave_room()
        self.last_check_on = curr_time
        try:
            self.save()
        except:
            print("room dissolved, all members offline")

    def dissolve(self):
        schedule('room.dissolve', 0, room_id=self.id)
        self.delete()

    def get_value(self, field_name):
        field_object = Room._meta.get_field(field_name)
        value = field_object.value_from_object(self)
        return value

    def broadcast(self, msg_type, **data):
        grp_id = 'room-'+str(self.id)
        live_event(group=grp_id, msg_type=msg_type, **data)

    def get_state_obj(self):
        state = {
            'room_id': self.id,
            'members_count': self.members.count(),
            'is_paused': self.is_paused,
            'current_roomtrack': self.current_roomtrack.get_obj(),
            'play_start_time': dump_datetime(self.play_start_time),
            'duration_to_complete': dump_datetime(self.duration_to_complete)
        }
        return state

    def get_title_obj(self, user):
        members = self.get_members()
        friends_found = []
        count = self.members.count()
        for member in members:
            friend_status, friend_obj = member.friendship_status(user)
            if friend_status == 3:
                friends_found.append(member.get_profile_min())
                if len(friends_found) > 2:
                    break
        return {'room_id': self.id, 'members_count': count, 'member_friends': friends_found}

    def get_roomtracks(self):
        rt = self.current_roomtrack
        List = [rt]
        for i in range(self.no_tracks-1):
            rt = rt.next_roomtrack
            List.append(rt)
        return List

    def get_members(self):
        members = self.members.all()
        return members

    def get_access_users(self):
        ausers = self.access_users.all()
        return ausers

    def can_user_access(self, user):
        try:
            self.access_users.get(id=user.id)
        except:
            return False
        else:
            return True

    def grant_access(self, user, save=True):
        self.access_users.add(user)
        if save:
            self.save()

    def revoke_access(self, user, save=True):
        self.access_users.remove(user)
        if save:
            self.save()

    def play(self, action_user=None):
        self.skip_to(self.current_roomtrack,
                     self.duration_to_complete, action_user=action_user)

    def pause(self, action_user=None):
        self.is_paused = True
        self.paused_on = timezone.now()
        start_time = self.play_start_time
        time_diff = timezone.now()-start_time
        time_left = dump_datetime(
            self.duration_to_complete)-time_diff.total_seconds()
        time_left = int(time_left)
        if time_left < 0:
            print(
                'PLAYBACK ERROR: Time played more than duration, possible SCHEDULED_SKIPTO_MISS', time_left)
            time_left = dump_datetime(self.current_roomtrack.track.duration)
        mins, secs = divmod(time_left, 60)
        self.duration_to_complete = datetime.time(0, mins, secs)
        self.play_start_time = timezone.now()
        print('pausing.. rt id', self.current_roomtrack.id)
        self.save()
        if action_user != None:
            action_user = action_user.get_profile_min()
        self.broadcast('update.playback.pause',
                       action_user=action_user, room=self.get_state_obj())

    def skip_to_next(self):
        curr_rt = self.current_roomtrack
        next_rt = curr_rt.next_roomtrack
        print('skiping to next', next_rt.id)
        self.skip_to(next_rt)

    def skip_to(self, roomtrack, duration=None, action_user=None):
        rt = roomtrack
        prev_rt = self.current_roomtrack
        self.current_roomtrack = rt
        if rt != prev_rt:
            track = rt.track
            track.plays_count += 1
            track.save()
        self.is_paused = False
        self.paused_on = None
        self.play_start_time = timezone.now()
        if duration != None:
            self.duration_to_complete = duration
        else:
            self.duration_to_complete = rt.track.duration
        self.save()
        # schedule next skip_to
        # timeout in ms
        schedule('skipto', (dump_datetime(self.duration_to_complete)-2)*1000,
                 room_id=self.get_value('id'))
        if action_user != None:
            action_user = action_user.get_profile_min()
        self.broadcast('update.playback.skipto',
                       action_user=action_user, room=self.get_state_obj())

    def add_track(self, track, action_user=None):
        # insert track between curr_last and curr
        curr = self.current_roomtrack
        curr_last = curr.previous_roomtrack
        curr_last.next_roomtrack = None
        curr_last.save()
        rt = RoomTrack.create(track, next_track=curr, room=self)
        curr_last.next_roomtrack = rt
        curr_last.save()
        self.no_tracks = RoomTrack.count(self)
        self.save()
        if action_user != None:
            action_user = action_user.get_profile_min()
        self.broadcast('update.tracks.add',
                       action_user=action_user, roomtrack=rt.get_obj())
        return rt

    def remove_roomtrack(self, roomtrack, action_user=None):
        # removes a roomtrack
        if self.no_tracks > 1:
            if self.current_roomtrack.id != roomtrack.id:
                obj = roomtrack.get_obj()
                prev = roomtrack.previous_roomtrack
                nxt = roomtrack.next_roomtrack
                roomtrack.next_roomtrack = None
                roomtrack.save()
                prev.next_roomtrack = nxt
                prev.save()
                roomtrack.delete()
                self.no_tracks = RoomTrack.count(self)
                self.save()
                if action_user != None:
                    action_user = action_user.get_profile_min()
                self.broadcast('update.tracks.remove',
                               action_user=action_user, roomtrack=obj)
                return True
            else:
                return False
        else:
            return False

    def get_roomtrack_by_index(self, index):
        rt = self.current_roomtrack
        for i in range(index):
            rt = rt.next_roomtrack
        return rt

    @classmethod
    def get_by_id(cls, pk):
        return cls.objects.get(id=pk)

    @classmethod
    def get_by_code(cls, code):
        return cls.objects.get(code=code)

    @classmethod
    def create(cls, tracks=[]):
        room = cls(created_on=timezone.now(), last_check_on=timezone.now(), play_start_time=timezone.now(),
                   is_paused=False, paused_on=None, no_tracks=0)
        room.current_roomtrack = RoomTrack.create(tracks[0])
        room.no_tracks = 1
        room.code = makecode()
        room.play_start_time = timezone.now()
        room.duration_to_complete = room.current_roomtrack.track.duration
        room.save()
        room.current_roomtrack.room = room
        room.current_roomtrack.save()
        tracks.pop(0)
        for track in tracks:
            room.add_track(track)
        return room


class RoomTrack(models.Model):
    added_on = models.DateTimeField()
    next_roomtrack = models.OneToOneField(
        "self", on_delete=models.SET_NULL, default=None, null=True, related_name="previous_roomtrack")
    room = models.ForeignKey(
        Room, on_delete=models.CASCADE, null=True, default=None, related_name="roomtracks")
    track = models.ForeignKey(
        'Track', on_delete=models.PROTECT, related_name="+")

    def get_obj(self):
        obj = self.track.get_obj()
        obj['roomtrack_id'] = self.id
        return obj

    @classmethod
    def create(cls, track, next_track=None, room=None):
        rt = cls(track=track, added_on=timezone.now(),
                 next_roomtrack=next_track, room=room)
        rt.save()
        if rt.next_roomtrack == None:
            rt.next_roomtrack = rt
            rt.save()
        return rt

    @classmethod
    def count(cls, room):
        return cls.objects.filter(room=room).count()

    @classmethod
    def get_by_id(cls, pk):
        return cls.objects.get(id=pk)


class Track(models.Model):
    added_on = models.DateTimeField()
    title = models.CharField(max_length=255)
    artists = models.CharField(max_length=255)
    duration = models.TimeField()
    plays_count = models.IntegerField()
    ref_id = models.CharField(max_length=255, default=None, null=True)
    storage_bucket = models.CharField(max_length=255)
    playback_path = models.CharField(max_length=255)
    image_path = models.CharField(max_length=255, default=None, null=True)

    def get_obj(self):
        playback_url = STORAGE_URLS[self.storage_bucket]+self.playback_path
        image_url = STORAGE_URLS[self.storage_bucket]+self.image_path
        obj = {'track_id': self.id, 'title': self.title, 'duration': dump_datetime(self.duration),
               'artists': self.artists, 'playback_url': playback_url, 'image_url': image_url}
        return obj

    @classmethod
    def get_by_id(cls, pk):
        return cls.objects.get(id=pk)

    @classmethod
    def get_by_ref_id(cls, ref_id):
        return cls.objects.get(ref_id=ref_id)

    @classmethod
    def browse(cls):
        tracks = cls.objects.all().order_by('-plays_count', '-added_on')[:25]
        return tracks

    @classmethod
    def create(cls, **values):
        track = cls(added_on=timezone.now(), **values)
        track.plays_count = 0
        track.save()
        return track


class Artist(models.Model):
    title = models.CharField(max_length=255)
