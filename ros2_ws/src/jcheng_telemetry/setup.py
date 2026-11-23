from setuptools import find_packages, setup

package_name = "jcheng_telemetry"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools", "PyYAML"],
    zip_safe=True,
    maintainer="JerichoCheng",
    maintainer_email="chengkewei77@gmail.com",
    description="Read-only robot telemetry and gateway for the JCHENG system",
    license="Proprietary (license review pending)",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "jcheng_telemetry_node = jcheng_telemetry.node:main",
            "jcheng_readonly_probe = jcheng_telemetry.probe:main",
        ]
    },
)
