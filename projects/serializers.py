from rest_framework import serializers

from accounts.serializers import PublicUserSerializer
from projects.models import Project, ProjectMember


class ProjectSerializer(serializers.ModelSerializer):
    my_role = serializers.CharField(read_only=True, required=False)
    member_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "key",
            "description",
            "status",
            "my_role",
            "member_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "status", "created_at", "updated_at"]


class ProjectCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    key = serializers.CharField(max_length=10)
    description = serializers.CharField(required=False, allow_blank=True, default="")


class ProjectUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    description = serializers.CharField(required=False, allow_blank=True)


class MemberSerializer(serializers.ModelSerializer):
    user = PublicUserSerializer(read_only=True)

    class Meta:
        model = ProjectMember
        fields = ["id", "user", "role", "joined_at"]
        read_only_fields = fields


class MemberAddSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=ProjectMember.Role.choices)


class MemberFilterSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ProjectMember.Role.choices, required=False)


class MemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ProjectMember.Role.choices)


class TransferOwnershipSerializer(serializers.Serializer):
    member_id = serializers.IntegerField()
